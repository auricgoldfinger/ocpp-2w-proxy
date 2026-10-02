"""Translate OCPP 1.6 transaction ids between the primary and secondary backend.

The charger only ever knows the primary's transactionId (it receives the primary's
StartTransaction.conf). The secondary backend issues its own id, so every message that
carries a transactionId has to be rewritten on the way to / from the secondary.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

from .ocpp import Call
from .state import StateStore

logger = logging.getLogger(__name__)

MAX_PENDING_STARTS = 100


class TransactionMap:
    def __init__(self, store: StateStore):
        self._store = store
        self._state = store.state

    # --- recording -------------------------------------------------------------------------

    def primary_started(self, start_ref: str, primary_tx: int) -> None:
        """The primary answered StartTransaction (start_ref = the charger's message id)."""
        secondary_tx = self._state.pending_secondary_starts.pop(start_ref, None)
        if secondary_tx is None:
            _put_capped(self._state.pending_primary_starts, start_ref, primary_tx)
        else:
            self._link(primary_tx, secondary_tx)
        self._store.save()

    def secondary_started(self, start_ref: str, secondary_tx: int) -> None:
        primary_tx = self._state.pending_primary_starts.pop(start_ref, None)
        if primary_tx is None:
            _put_capped(self._state.pending_secondary_starts, start_ref, secondary_tx)
        else:
            self._link(primary_tx, secondary_tx)
        self._store.save()

    def primary_start_failed(self, start_ref: str) -> None:
        """The primary never confirmed this start; a secondary id for it can never be linked."""
        if self._state.pending_secondary_starts.pop(start_ref, None) is not None:
            logger.warning("secondary transaction for start %s has no primary counterpart", start_ref)
            self._store.save()

    def forget(self, primary_tx: int) -> None:
        if self._state.transactions.pop(str(primary_tx), None) is not None:
            self._store.save()

    def _link(self, primary_tx: int, secondary_tx: int) -> None:
        logger.info("transaction %s (primary) <-> %s (secondary)", primary_tx, secondary_tx)
        self._state.transactions[str(primary_tx)] = secondary_tx

    # --- lookup ----------------------------------------------------------------------------

    def to_secondary(self, primary_tx: int) -> int | None:
        return self._state.transactions.get(str(primary_tx))

    def to_primary(self, secondary_tx: int) -> int | None:
        for primary_tx, mapped in self._state.transactions.items():
            if mapped == secondary_tx:
                return int(primary_tx)
        return None

    # --- rewriting -------------------------------------------------------------------------

    def rewrite_for_secondary(self, call: Call) -> Call | None:
        """Charger -> secondary. None means: do not send (it cannot be made meaningful)."""
        rewrite = _TO_SECONDARY.get(call.action)
        return rewrite(self, call) if rewrite else call

    def rewrite_for_charger(self, call: Call) -> Call | None:
        """Secondary -> charger. None means: unknown transaction, reject it."""
        rewrite = _TO_CHARGER.get(call.action)
        return rewrite(self, call) if rewrite else call


def _put_capped(pending: dict[str, int], key: str, value: int) -> None:
    pending[key] = value
    while len(pending) > MAX_PENDING_STARTS:
        dropped = next(iter(pending))
        logger.warning("dropping unmatched pending start %s", dropped)
        del pending[dropped]


def _meter_values_to_secondary(txmap: TransactionMap, call: Call) -> Call:
    if "transactionId" not in call.payload:
        return call
    secondary_tx = txmap.to_secondary(call.payload["transactionId"])
    payload = dict(call.payload)
    if secondary_tx is None:
        # Still useful to the backend as connector meter readings, just not tied to a session.
        del payload["transactionId"]
    else:
        payload["transactionId"] = secondary_tx
    return call.with_payload(payload)


def _stop_transaction_to_secondary(txmap: TransactionMap, call: Call) -> Call | None:
    secondary_tx = txmap.to_secondary(call.payload.get("transactionId"))
    if secondary_tx is None:
        logger.error(
            "StopTransaction for primary transaction %s has no secondary counterpart; not sent to secondary",
            call.payload.get("transactionId"),
        )
        return None
    return call.with_payload({**call.payload, "transactionId": secondary_tx})


def _remote_stop_to_charger(txmap: TransactionMap, call: Call) -> Call | None:
    primary_tx = txmap.to_primary(call.payload.get("transactionId"))
    if primary_tx is None:
        return None
    return call.with_payload({**call.payload, "transactionId": primary_tx})


_TO_SECONDARY: Mapping[str, Callable[[TransactionMap, Call], Call | None]] = {
    "MeterValues": _meter_values_to_secondary,
    "StopTransaction": _stop_transaction_to_secondary,
}

_TO_CHARGER: Mapping[str, Callable[[TransactionMap, Call], Call | None]] = {
    "RemoteStopTransaction": _remote_stop_to_charger,
}
