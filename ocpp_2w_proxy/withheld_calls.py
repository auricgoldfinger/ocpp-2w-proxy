"""Charger calls the primary backend must never see: those of an unauthorized idTag.

Some chargers start a session with an empty or all-zeros idTag the moment a car is
plugged in. The primary would record it as a session of its own; instead the proxy
answers the charger itself with "Invalid", so the charger ends it as it would for an
unknown card. The secondaries still receive these calls, so they can tell it happened.
The session's transactionId is made up by the proxy, so its MeterValues and
StopTransaction are withheld from the primary as well.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from .ocpp import Call, CallResult, new_message_id
from .transactions import TransactionMap

_INVALID = {"status": "Invalid"}


@dataclass(frozen=True)
class Withheld:
    """The proxy's own answer to the charger; start_ref links a withheld start for the secondaries."""

    reply: CallResult
    start_ref: str | None = None


def is_unauthorized_id_tag(tag: Any) -> bool:
    return isinstance(tag, str) and not tag.strip("0")


def withhold(call: Call, transactions: TransactionMap) -> Withheld | None:
    """The proxy's answer if this call must not reach the primary; None if it goes there as usual."""
    handler = _HANDLERS.get(call.action)
    return handler(call, transactions) if handler else None


def _authorize(call: Call, transactions: TransactionMap) -> Withheld | None:
    if not is_unauthorized_id_tag(call.payload.get("idTag")):
        return None
    return Withheld(CallResult(call.id, {"idTagInfo": dict(_INVALID)}))


def _start_transaction(call: Call, transactions: TransactionMap) -> Withheld | None:
    if not is_unauthorized_id_tag(call.payload.get("idTag")):
        return None
    start_ref = new_message_id()
    transaction_id = transactions.withheld_started(start_ref)
    return Withheld(CallResult(call.id, {"transactionId": transaction_id, "idTagInfo": dict(_INVALID)}), start_ref)


def _meter_values(call: Call, transactions: TransactionMap) -> Withheld | None:
    if not transactions.is_withheld(call.payload.get("transactionId")):
        return None
    return Withheld(CallResult(call.id, {}))


def _stop_transaction(call: Call, transactions: TransactionMap) -> Withheld | None:
    transaction_id = call.payload.get("transactionId")
    if not transactions.is_withheld(transaction_id):
        return None
    transactions.withheld_stopped(transaction_id)
    return Withheld(CallResult(call.id, {}))


_HANDLERS: Mapping[str, Callable[[Call, TransactionMap], Withheld | None]] = {
    "Authorize": _authorize,
    "StartTransaction": _start_transaction,
    "MeterValues": _meter_values,
    "StopTransaction": _stop_transaction,
}
