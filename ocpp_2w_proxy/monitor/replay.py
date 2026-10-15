"""Rebuilding an exchange from its stored rows (pure): legs are computed on read by the same projection the
writer uses, so the list, the detail drawer and the live stream always agree.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, NamedTuple

from ..reasons import Reason
from .events import FlowEvent
from .projection import DEFAULT_STALE_AFTER, ExchangeProjection
from .rows import CALL, ERROR, decision_event, frame_event, frame_message


class StoredFrame(NamedTuple):
    at: float
    peer: str
    dir: str
    msg_type: str
    wire_id: str
    payload: Any  # decoded JSON; None when the frame equals the origin payload


class StoredDecision(NamedTuple):
    at: float
    peer: str
    reason: str
    detail: str | None  # JSON text


class StoredExchange(NamedTuple):
    exchange_id: int
    charger_id: str
    origin: str | None
    action: str | None
    started_at: float
    incomplete: bool
    not_sent: tuple[str, ...]
    frames: Sequence[StoredFrame]
    decisions: Sequence[StoredDecision]


def origin_payload(frames: Sequence[StoredFrame]) -> Any:
    """The origin frame is the first Call; it is always stored whole."""
    return next((frame.payload for frame in frames if frame.msg_type == CALL and frame.payload is not None), None)


def project(stored: StoredExchange, stale_after: float = DEFAULT_STALE_AFTER) -> ExchangeProjection:
    projection = ExchangeProjection(stored.exchange_id, stale_after=stale_after)
    projection.charger_id = stored.charger_id
    projection.known_origin = stored.origin
    projection.started_at = stored.started_at
    projection.not_sent = stored.not_sent
    projection.incomplete = stored.incomplete
    for event in _events(stored):
        projection.apply(event)
    return projection


def frame_views(frames: Sequence[StoredFrame]) -> list[dict]:
    origin = origin_payload(frames)
    return [_frame_view(frame, origin) for frame in frames]


def decision_views(decisions: Sequence[StoredDecision]) -> list[dict]:
    return [_decision_view(decision) for decision in decisions]


def _events(stored: StoredExchange) -> list[FlowEvent]:
    """Frames and decisions merged by time; on a tie the frame goes first, as it does on the relay path."""
    origin = origin_payload(stored.frames)
    timed: list[tuple[float, int, FlowEvent]] = []
    for frame in stored.frames:
        payload = origin if frame.payload is None else frame.payload
        message = frame_message(frame.msg_type, frame.wire_id, stored.action, payload)
        timed.append((frame.at, 0, frame_event(stored.charger_id, frame.at, frame.peer, frame.dir, message)))
    for decision in stored.decisions:
        event = decision_event(stored.charger_id, *decision)
        if event is not None:
            timed.append((decision.at, 1, event))
    timed.sort(key=lambda item: item[:2])
    return [event for _, _, event in timed]


def _frame_view(frame: StoredFrame, origin: Any) -> dict:
    payload = origin if frame.payload is None else frame.payload
    error = None
    if frame.msg_type == ERROR:
        error = {"code": payload.get("code"), "description": payload.get("description")}
        payload = payload.get("details", {})
    return {
        "at": frame.at,
        "peer": frame.peer,
        "dir": frame.dir,
        "msg_type": frame.msg_type,
        "wire_id": frame.wire_id,
        "payload": payload,
        "same_as_origin": frame.payload is None,
        "error": error,
    }


def _decision_view(decision: StoredDecision) -> dict:
    detail = None if decision.detail is None else json.loads(decision.detail)
    reason = Reason.__members__.get(decision.reason)
    return {
        "at": decision.at,
        "peer": decision.peer,
        "reason": decision.reason,
        "text": None if reason is None else reason.describe(decision.peer, detail),
        "detail": detail,
    }
