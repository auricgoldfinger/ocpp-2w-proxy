"""
How much a charger message matters while a backend cannot take it right away.

  DURABLE   billing data: queued in order, kept across restarts (StopTransaction and the
            MeterValues of a transaction).
  LATEST    state reports: only the newest one per subject matters, so a newer report
            replaces an older one instead of queueing behind it.
  DROPPABLE stale as soon as it is late: a Heartbeat, or a meter reading that belongs to
            no transaction. Dropped while the backend is away.
  LIVE      needs the backend's own answer (Authorize, StartTransaction, ...): it is never
            queued or answered by the proxy. For the primary it waits for a reconnect within
            its timeout; if that fails the charger connection is closed, so the charger
            handles the message by its own offline rules.
"""

from __future__ import annotations

from enum import Enum, auto

from .ocpp import Call


class MessageClass(Enum):
    DURABLE = auto()
    LATEST = auto()
    DROPPABLE = auto()
    LIVE = auto()


_LATEST_ACTIONS = frozenset({"StatusNotification", "FirmwareStatusNotification", "DiagnosticsStatusNotification"})


def classify(call: Call) -> MessageClass:
    match call.action:
        case "StopTransaction":
            return MessageClass.DURABLE
        case "MeterValues":
            return MessageClass.DURABLE if "transactionId" in call.payload else MessageClass.DROPPABLE
        case "Heartbeat":
            return MessageClass.DROPPABLE
        case action if action in _LATEST_ACTIONS:
            return MessageClass.LATEST
        case _:
            return MessageClass.LIVE


def latest_key(call: Call) -> str:
    """Which earlier report a LATEST call replaces: the same action for the same connector."""
    return f"{call.action}:{call.payload.get('connectorId', 0)}"
