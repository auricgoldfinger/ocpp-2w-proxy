import json

import pytest

from ocpp_2w_proxy.monitor.events import EventKind, FlowEvent
from ocpp_2w_proxy.monitor.projection import ExchangeProjection
from ocpp_2w_proxy.monitor.rows import (
    ExchangeRow,
    decision_event,
    decision_row,
    frame_event,
    frame_message,
    frame_row,
)
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason

ORIGIN = {"connectorId": 1, "transactionId": 10216}


def event(kind, message, peer="primary", at=1.0, reason=None, detail=None):
    return FlowEvent(kind, at, "cp", peer, message, reason, detail)


@pytest.mark.parametrize(
    ("message", "origin", "msg_type", "payload"),
    [
        (Call("w1", "MeterValues", ORIGIN), ORIGIN, "call", None),
        (Call("w1", "MeterValues", dict(ORIGIN)), ORIGIN, "call", None),
        (Call("w1", "MeterValues", ORIGIN), None, "call", ORIGIN),
        (Call("w1", "MeterValues", {**ORIGIN, "transactionId": 5}), ORIGIN, "call", {**ORIGIN, "transactionId": 5}),
        (CallResult("w1", {}), {}, "result", {}),
        (CallError("w1", "NotSupported", "no", {"a": 1}), ORIGIN, "error", None),
    ],
)
def test_frame_row_round_trip(message, origin, msg_type, payload):
    row = frame_row(7, event(EventKind.SENT, message), origin)

    assert row[:6] == (7, 1.0, "primary", "out", msg_type, "w1")
    if msg_type != "error":
        assert (None if row.payload is None else json.loads(row.payload)) == payload
    stored = json.loads(row.payload) if row.payload else origin
    rebuilt = frame_event(
        "cp", row.at, row.peer, row.dir, frame_message(row.msg_type, row.wire_id, "MeterValues", stored)
    )
    assert rebuilt == event(EventKind.SENT, message)


def test_received_frames_are_stored_as_in():
    assert frame_row(1, event(EventKind.RECEIVED, CallResult("w1", {})), None).dir == "in"


def test_frame_row_rejects_events_without_a_message():
    with pytest.raises(ValueError):
        frame_row(1, event(EventKind.LINK, None), None)


def test_decision_row_round_trip():
    row = decision_row(7, event(EventKind.DECIDED, None, reason=Reason.UNKNOWN_TRANSACTION, detail=77))

    assert row == (7, 1.0, "primary", "UNKNOWN_TRANSACTION", "77")
    assert decision_event("cp", *row[1:]) == event(
        EventKind.DECIDED, None, reason=Reason.UNKNOWN_TRANSACTION, detail=77
    )


def test_decision_without_detail():
    row = decision_row(7, event(EventKind.DECIDED, None, reason=Reason.QUEUED))

    assert row.detail is None
    assert decision_event("cp", *row[1:]).detail is None


def test_unknown_reason_code_is_skipped_on_read():
    assert decision_event("cp", 1.0, "primary", "RETIRED_CODE", None) is None


def test_exchange_row_from_summary():
    projection = ExchangeProjection(9)
    projection.not_sent = ("stats",)
    projection.apply(event(EventKind.RECEIVED, Call("w1", "MeterValues", ORIGIN), peer="charger"))

    row = ExchangeRow.from_summary(projection.summary())

    assert row.exchange_id == 9
    assert row.origin == "charger"
    assert row.transaction_id == 10216
    assert row.not_sent == ("stats",)
