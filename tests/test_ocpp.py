import json

import pytest

from ocpp_2w_proxy.ocpp import Call, CallError, CallResult, ProtocolError, parse, protocol_error_reply, serialize


def test_round_trip_all_message_types():
    for message in (
        Call("1", "Heartbeat", {}),
        CallResult("1", {"currentTime": "x"}),
        CallError("1", "NotSupported", "nope", {"a": 1}),
    ):
        assert parse(serialize(message)) == message


@pytest.mark.parametrize(
    "frame",
    [
        "not json",
        "{}",
        "[2]",
        '[2, 1, "Heartbeat", {}]',  # numeric id
        '[2, "1", "Heartbeat", []]',  # payload not an object
        '[2, "1", "", {}]',  # empty action
        '[3, "1", "x"]',
        '[4, "1", "Code", "desc"]',
        '[9, "1", {}]',
        json.dumps([2, "x" * 37, "Heartbeat", {}]),
    ],
)
def test_malformed_frames_raise_protocol_error(frame):
    with pytest.raises(ProtocolError):
        parse(frame)


def test_protocol_error_salvages_the_message_id():
    with pytest.raises(ProtocolError) as exc:
        parse(json.dumps([2, "x", "Heartbeat"]))  # a Call missing its payload
    assert exc.value.message_id == "x"

    with pytest.raises(ProtocolError) as unreadable:
        parse("this is not ocpp")
    assert unreadable.value.message_id is None


def test_protocol_error_reply_uses_the_salvaged_id():
    error = ProtocolError("Call must have 4 elements", "x")
    assert protocol_error_reply(error) == CallError("x", "ProtocolError", "Call must have 4 elements")
    anonymous = ProtocolError("not valid JSON: ...")
    reply = protocol_error_reply(anonymous)
    assert isinstance(reply, CallError) and reply.id != "x"
