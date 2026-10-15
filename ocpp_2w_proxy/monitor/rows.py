"""How flow events are stored as rows, and rebuilt from them so the read path can project them again.

A frame's payload is kept as JSON text here; the database layer compresses it in its own thread. A Call frame
whose payload equals the origin payload is stored without one (None), and gets the origin's back on read.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, NamedTuple

from ..ocpp import Call, CallError, CallResult, Message
from ..reasons import Reason
from .events import EventKind, FlowEvent
from .gaps import Gap

IN = "in"
OUT = "out"
CALL = "call"
RESULT = "result"
ERROR = "error"

_DIRECTIONS = {EventKind.RECEIVED: IN, EventKind.SENT: OUT}
_KINDS = {IN: EventKind.RECEIVED, OUT: EventKind.SENT}


class ExchangeRow(NamedTuple):
    exchange_id: int
    charger_id: str
    origin: str | None
    action: str | None
    started_at: float
    last_at: float
    outcome: str | None
    problem: bool
    incomplete: bool
    transaction_id: int | None
    connector_id: int | None
    id_tag: str | None
    not_sent: tuple[str, ...]
    headline: str | None

    @classmethod
    def from_summary(cls, summary: Mapping[str, Any]) -> ExchangeRow:
        return cls(
            exchange_id=int(summary["id"]),
            **{field: summary[field] for field in cls._fields[1:] if field != "not_sent"},
            not_sent=tuple(summary["not_sent"]),
        )


class FrameRow(NamedTuple):
    exchange_id: int
    at: float
    peer: str
    dir: str
    msg_type: str
    wire_id: str
    payload: str | None  # JSON text; None when equal to the origin payload


class DecisionRow(NamedTuple):
    exchange_id: int
    at: float
    peer: str
    reason: str
    detail: str | None  # JSON text


class LinkRow(NamedTuple):
    charger_id: str
    peer: str
    at: float
    up: bool


class WriteBatch(NamedTuple):
    """Everything one writer batch hands to the database, already projected."""

    exchanges: Sequence[ExchangeRow] = ()
    frames: Sequence[FrameRow] = ()
    decisions: Sequence[DecisionRow] = ()
    links: Sequence[LinkRow] = ()
    gaps: Sequence[Gap] = ()
    incomplete: Sequence[int] = ()  # exchange ids a monitor gap overlapped


# --- events -> rows ----------------------------------------------------------------------


def frame_row(exchange_id: int, event: FlowEvent, origin: Mapping[str, Any] | None) -> FrameRow:
    """``origin`` is the payload to compare against; pass None for the origin frame itself, which is kept whole."""
    message = event.message
    match message:
        case Call(payload=payload):
            msg_type, body = CALL, None if origin is not None and (payload is origin or payload == origin) else payload
        case CallResult(payload=payload):
            msg_type, body = RESULT, payload
        case CallError(code=code, description=description, details=details):
            msg_type, body = ERROR, {"code": code, "description": description, "details": details}
        case _:
            raise ValueError(f"not a frame event: {event.kind}")
    text = None if body is None else json.dumps(body, separators=(",", ":"))
    return FrameRow(exchange_id, event.at, event.peer, _DIRECTIONS[event.kind], msg_type, message.id, text)


def decision_row(exchange_id: int, event: FlowEvent) -> DecisionRow:
    if event.reason is None:
        raise ValueError("a decision event needs a reason")
    detail = None if event.detail is None else json.dumps(event.detail, default=str)
    return DecisionRow(exchange_id, event.at, event.peer, event.reason.name, detail)


# --- rows -> events ----------------------------------------------------------------------


def frame_message(msg_type: str, wire_id: str, action: str | None, payload: Any) -> Message:
    """The message a stored frame held; ``payload`` is already decoded (and filled in from the origin)."""
    match msg_type:
        case "call":
            return Call(wire_id, action or "", payload)
        case "result":
            return CallResult(wire_id, payload)
        case _:
            return CallError(
                wire_id, payload.get("code", ""), payload.get("description", ""), payload.get("details", {})
            )


def frame_event(charger_id: str, at: float, peer: str, direction: str, message: Message) -> FlowEvent:
    return FlowEvent(_KINDS[direction], at, charger_id, peer, message, None, None)


def decision_event(charger_id: str, at: float, peer: str, reason: str, detail: str | None) -> FlowEvent | None:
    """None for a reason code this version no longer knows."""
    known = Reason.__members__.get(reason)
    if known is None:
        return None
    return FlowEvent(
        EventKind.DECIDED, at, charger_id, peer, None, known, None if detail is None else json.loads(detail)
    )
