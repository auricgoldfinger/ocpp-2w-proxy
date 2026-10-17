"""The shape of one flow event as the recorder enqueues it and the writer consumes it.

On the relay path an event is a plain tuple (cheapest to build); the writer views it as a
FlowEvent with ``FlowEvent._make(raw)``. Field order is the contract between the two.
"""

from __future__ import annotations

from enum import StrEnum
from typing import NamedTuple

from ..ocpp import Message
from ..reasons import Reason


class EventKind(StrEnum):
    RECEIVED = "received"  # message: the frame read from ``peer``
    SENT = "sent"  # message: the frame written to ``peer``
    DECIDED = "decided"  # message: the Call; reason and detail set
    LINK = "link"  # message None; detail: (up, note), up True when the link to ``peer`` came up; note str | None


class FlowEvent(NamedTuple):
    kind: EventKind
    at: float  # time.time()
    charger_id: str
    peer: str  # "charger" or a backend name
    message: Message | None
    reason: Reason | None
    detail: object
