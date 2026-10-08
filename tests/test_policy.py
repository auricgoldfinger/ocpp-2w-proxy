import pytest

from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.policy import (
    PRIMARY_DEFAULT_RULE,
    PRIMARY_DEFAULT_RULES,
    SECONDARY_DEFAULT_RULE,
    SECONDARY_DEFAULT_RULES,
    CommandPolicy,
    Rule,
)

SECONDARY = CommandPolicy(
    SECONDARY_DEFAULT_RULES, SECONDARY_DEFAULT_RULE, frozenset({"MeterValueSampleInterval"}), True
)
PRIMARY = CommandPolicy(PRIMARY_DEFAULT_RULES, PRIMARY_DEFAULT_RULE)


@pytest.mark.parametrize(
    "action",
    ["RemoteStopTransaction", "TriggerMessage", "GetConfiguration", "UnlockConnector", "GetCompositeSchedule"],
)
def test_secondary_forwards_harmless_commands(action):
    call = Call("1", action, {"connectorId": 1})
    assert SECONDARY.decide(call) == call


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("RemoteStartTransaction", {"status": "Rejected"}),
        ("SetChargingProfile", {"status": "Rejected"}),
        ("ClearChargingProfile", {"status": "Unknown"}),
        ("ChangeAvailability", {"status": "Rejected"}),
        ("Reset", {"status": "Rejected"}),
        ("SendLocalList", {"status": "NotSupported"}),
        ("GetLocalListVersion", {"listVersion": -1}),
        ("UpdateFirmware", {}),
        ("ChangeConfiguration", {"status": "Rejected"}),
    ],
)
def test_secondary_gets_canned_answers_for_conflicting_commands(action, expected):
    assert SECONDARY.decide(Call("7", action, {"key": "HeartbeatInterval"})) == CallResult("7", expected)


def test_secondary_unknown_action_is_not_supported():
    decision = SECONDARY.decide(Call("7", "SomeVendorThing", {}))
    assert isinstance(decision, CallError)
    assert decision.code == "NotSupported"


def test_change_configuration_allowlisted_key_is_forwarded():
    call = Call("1", "ChangeConfiguration", {"key": "MeterValueSampleInterval", "value": "60"})
    assert SECONDARY.decide(call) == call


def test_remote_start_charging_profile_is_stripped_when_forwarded():
    policy = CommandPolicy({"RemoteStartTransaction": Rule.FORWARD}, Rule.ERROR, strip_charging_profile=True)
    call = Call("1", "RemoteStartTransaction", {"idTag": "ABC", "chargingProfile": {"x": 1}})
    assert policy.decide(call).payload == {"idTag": "ABC"}


def test_primary_forwards_everything_by_default():
    for action in ("SetChargingProfile", "Reset", "Whatever"):
        call = Call("1", action, {"chargingProfile": {}})
        assert PRIMARY.decide(call) == call


def test_answer_rule_requires_a_canned_answer():
    with pytest.raises(ValueError):
        CommandPolicy({"Foo": Rule.ANSWER}, Rule.ERROR)
