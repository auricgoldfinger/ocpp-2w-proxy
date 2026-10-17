import json

import pytest
from monitor_events import decided, link, received, sent

from ocpp_2w_proxy.monitor.rows import decision_event, decision_row, frame_event, frame_message, frame_row
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason

ORIGIN = {"connectorId": 1, "transactionId": 10216}


@pytest.mark.parametrize(("flow", "direction"), [(sent, "out"), (received, "in")])
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
def test_frame_row_round_trip(flow, direction, message, origin, msg_type, payload):
    row = frame_row(7, flow(1.0, "primary", message), origin)

    assert row[:6] == (7, 1.0, "primary", direction, msg_type, "w1")
    if msg_type != "error":
        assert (None if row.payload is None else json.loads(row.payload)) == payload
    stored = json.loads(row.payload) if row.payload else origin
    rebuilt = frame_event(
        "cp", row.at, row.peer, row.dir, frame_message(row.msg_type, row.wire_id, "MeterValues", stored)
    )
    assert rebuilt == flow(1.0, "primary", message)


def test_frame_row_rejects_events_without_a_message():
    with pytest.raises(ValueError):
        frame_row(1, link(1.0, "primary", False), None)


@pytest.mark.parametrize(("detail", "stored"), [(77, "77"), (None, None)])
def test_decision_row_round_trip(detail, stored):
    decision = decided(1.0, "primary", Reason.UNKNOWN_TRANSACTION, detail)
    row = decision_row(7, decision)

    assert row == (7, 1.0, "primary", "UNKNOWN_TRANSACTION", stored)
    assert decision_event("cp", *row[1:]) == decision


def test_unknown_reason_code_is_skipped_on_read():
    assert decision_event("cp", 1.0, "primary", "RETIRED_CODE", None) is None
