"""Translate OCPP 1.6 transaction ids between the primary and each secondary backend.

The charger only ever knows the primary's transactionId (it receives the primary's
StartTransaction.conf). Each secondary backend issues its own id, so every message that
carries a transactionId has to be rewritten on the way to / from that backend.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from .ocpp import Call
from .state import StateStore

logger = logging.getLogger(__name__)

MAX_PENDING_STARTS = 100
# Proxy-made transactionIds count down from the int32 maximum: positive (charger firmware
# may not take a negative id) and far from the ids a backend hands out.
FIRST_WITHHELD_TX = 2147483647


class TransactionMap:
    def __init__(self, store: StateStore):
        self._store = store
        self._state = store.state

    # --- recording -------------------------------------------------------------------------

    def primary_started(self, start_ref: str, primary_tx: int) -> None:
        """The primary answered StartTransaction (start_ref = the proxy's own reference for that start)."""
        self._evict_withheld(primary_tx)
        self._record_start(start_ref, primary_tx)
        self._store.save()

    def withheld_started(self, start_ref: str) -> int:
        """A start the primary never sees: the proxy makes up its transactionId.

        Secondary backends link their ids to it just as they would to a primary's id.
        """
        transaction_id = self._allocate_withheld()
        self._record_start(start_ref, transaction_id)
        withheld = self._state.withheld_transactions
        withheld.append(transaction_id)
        while len(withheld) > MAX_PENDING_STARTS:
            logger.warning("forgetting withheld transaction %s", withheld.pop(0))
        self._store.save()
        return transaction_id

    def withheld_stopped(self, transaction_id: int) -> None:
        if transaction_id in self._state.withheld_transactions:
            self._state.withheld_transactions.remove(transaction_id)
            self._store.save()

    def is_withheld(self, transaction_id: object) -> bool:
        return transaction_id in self._state.withheld_transactions

    def _allocate_withheld(self) -> int:
        in_use = (
            {int(tx) for tx in self._state.transactions}
            | set(self._state.pending_primary_starts.values())
            | set(self._state.withheld_transactions)
        )
        candidate = self._state.next_withheld_tx
        while candidate in in_use or candidate < 1:
            candidate = FIRST_WITHHELD_TX if candidate <= 1 else candidate - 1
        self._state.next_withheld_tx = candidate - 1
        return candidate

    def _evict_withheld(self, primary_tx: int) -> None:
        """The primary reuses a withheld id: the real session wins, its billing data must
        reach the primary. The withheld session was stopped long ago (it got Invalid)."""
        if primary_tx not in self._state.withheld_transactions:
            return
        logger.error("primary transaction %s reuses a withheld id; the withheld session is forgotten", primary_tx)
        self._state.withheld_transactions.remove(primary_tx)
        self._state.transactions.pop(str(primary_tx), None)
        for ref in [ref for ref, tx in self._state.pending_primary_starts.items() if tx == primary_tx]:
            del self._state.pending_primary_starts[ref]

    def _record_start(self, start_ref: str, primary_tx: int) -> None:
        links = {
            name: pending.pop(start_ref)
            for name, pending in self._state.pending_secondary_starts.items()
            if start_ref in pending
        }
        if links:
            self._link(primary_tx, links)
        # Keep the record: secondary backends that answer later still link through it.
        _put_capped(self._state.pending_primary_starts, start_ref, primary_tx)

    def secondary_started(self, backend_name: str, start_ref: str, secondary_tx: int) -> None:
        primary_tx = self._state.pending_primary_starts.get(start_ref)
        if primary_tx is None:
            pending = self._state.pending_secondary_starts.setdefault(backend_name, {})
            _put_capped(pending, start_ref, secondary_tx)
        else:
            self._link(primary_tx, {backend_name: secondary_tx})
        self._store.save()

    def primary_start_failed(self, start_ref: str) -> None:
        """The primary never confirmed this start; secondary ids for it can never be linked."""
        if any(pending.pop(start_ref, None) is not None for pending in self._state.pending_secondary_starts.values()):
            logger.warning("secondary transactions for start %s have no primary counterpart", start_ref)
            self._store.save()

    def forget(self, primary_tx: int, backend_name: str) -> None:
        """One backend confirmed its StopTransaction: drop that backend's link only.

        Other backends may still owe their Start/Stop answers, so the transaction
        record and the pending start stay until the last link ends. The pending
        starts are capped (_put_capped), so slow backends cannot grow them unbounded.
        """
        links = self._state.transactions.get(str(primary_tx))
        if links is None:
            return
        links.pop(backend_name, None)
        if not links:
            # No backend still holds a link; a late Start answer may recreate the
            # record through the pending start, which is kept for exactly that.
            del self._state.transactions[str(primary_tx)]
        self._store.save()

    def _link(self, primary_tx: int, links: Mapping[str, int]) -> None:
        logger.info("transaction %s (primary) <-> %s", primary_tx, links)
        self._state.transactions.setdefault(str(primary_tx), {}).update(links)

    # --- lookup ----------------------------------------------------------------------------

    def to_secondary(self, primary_tx: int, backend_name: str) -> int | None:
        return self._state.transactions.get(str(primary_tx), {}).get(backend_name)

    def to_primary(self, secondary_tx: int, backend_name: str) -> int | None:
        for primary_tx, links in self._state.transactions.items():
            if links.get(backend_name) == secondary_tx:
                return int(primary_tx)
        return None

    # --- rewriting -------------------------------------------------------------------------

    def rewrite_for_secondary(self, call: Call, backend_name: str) -> Call | None:
        """Charger -> secondary. None means: do not send (it cannot be made meaningful)."""
        rewrite = _TO_SECONDARY.get(call.action)
        return rewrite(self, call, backend_name) if rewrite else call

    def rewrite_for_charger(self, call: Call, backend_name: str) -> Call | None:
        """Secondary -> charger. None means: unknown transaction, reject it."""
        rewrite = _TO_CHARGER.get(call.action)
        return rewrite(self, call, backend_name) if rewrite else call


def _put_capped(pending: dict[str, int], key: str, value: int) -> None:
    pending[key] = value
    while len(pending) > MAX_PENDING_STARTS:
        dropped = next(iter(pending))
        logger.warning("dropping unmatched pending start %s", dropped)
        del pending[dropped]


def _meter_values_to_secondary(txmap: TransactionMap, call: Call, backend_name: str) -> Call:
    if "transactionId" not in call.payload:
        return call
    secondary_tx = txmap.to_secondary(call.payload["transactionId"], backend_name)
    payload = dict(call.payload)
    if secondary_tx is None:
        # Still useful to the backend as connector meter readings, just not tied to a session.
        del payload["transactionId"]
    else:
        payload["transactionId"] = secondary_tx
    return call.with_payload(payload)


def _stop_transaction_to_secondary(txmap: TransactionMap, call: Call, backend_name: str) -> Call | None:
    secondary_tx = txmap.to_secondary(call.payload.get("transactionId"), backend_name)
    if secondary_tx is None:
        logger.error(
            "StopTransaction for primary transaction %s has no %s counterpart; not sent to that backend",
            call.payload.get("transactionId"),
            backend_name,
        )
        return None
    return call.with_payload({**call.payload, "transactionId": secondary_tx})


def _remote_stop_to_charger(txmap: TransactionMap, call: Call, backend_name: str) -> Call | None:
    primary_tx = txmap.to_primary(call.payload.get("transactionId"), backend_name)
    if primary_tx is None:
        return None
    return call.with_payload({**call.payload, "transactionId": primary_tx})


def _set_charging_profile_to_charger(txmap: TransactionMap, call: Call, backend_name: str) -> Call | None:
    """A TxProfile names the transaction it applies to: that id is the backend's own
    transactionId and must be translated, or the profile applies to nothing."""
    profile = call.payload.get("csChargingProfiles")
    if not isinstance(profile, dict):
        return call
    secondary_tx = profile.get("transactionId")
    if secondary_tx is None:
        return call  # TxDefaultProfile / ChargeProfileMaxStackLevel: no transaction to translate
    primary_tx = txmap.to_primary(secondary_tx, backend_name)
    if primary_tx is None:
        return None
    return call.with_payload({**call.payload, "csChargingProfiles": {**profile, "transactionId": primary_tx}})


_TO_SECONDARY: Mapping[str, Callable[[TransactionMap, Call, str], Call | None]] = {
    "MeterValues": _meter_values_to_secondary,
    "StopTransaction": _stop_transaction_to_secondary,
}

_TO_CHARGER: Mapping[str, Callable[[TransactionMap, Call, str], Call | None]] = {
    "RemoteStopTransaction": _remote_stop_to_charger,
    "SetChargingProfile": _set_charging_profile_to_charger,
}
