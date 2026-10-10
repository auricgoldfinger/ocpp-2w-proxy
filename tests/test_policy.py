import pytest

from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.policy import (
    CHARGER_BOUND_ACTIONS,
    PRIMARY_DEFAULT_RULE,
    PRIMARY_DEFAULT_RULES,
    SECONDARY_DEFAULT_RULE,
    SECONDARY_DEFAULT_RULES,
    CommandPolicy,
    Rule,
)

SECONDARY = CommandPolicy(
    SECONDARY_DEFAULT_RULES, SECONDARY_DEFAULT_RULE, frozenset({"metervaluesampleinterval"}), True
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


def test_change_configuration_keys_match_case_insensitively():
    """OCPP configuration keys are CiStrings: the charger may spell them in any case."""
    shouty = Call("1", "ChangeConfiguration", {"key": "METERVALUESAMPLEINTERVAL", "value": "60"})
    assert SECONDARY.decide(shouty) == shouty
    denied = Call("1", "ChangeConfiguration", {"key": "LocalAuthListEnabled", "value": "true"})
    assert SECONDARY.decide(denied) == CallResult("1", {"status": "Rejected"})


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


def test_primary_cannot_change_a_key_owned_by_a_secondary():
    primary = CommandPolicy(
        PRIMARY_DEFAULT_RULES, PRIMARY_DEFAULT_RULE, withheld_configuration_keys=frozenset({"maxcurrent"})
    )
    owned = Call("1", "ChangeConfiguration", {"key": "MaxCurrent", "value": "0"})
    other = Call("2", "ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"})
    assert primary.decide(owned) == CallResult("1", {"status": "Rejected"})
    assert primary.decide(other) == other


def test_charger_bound_actions_are_the_nineteen_a_central_system_can_send():
    assert len(CHARGER_BOUND_ACTIONS) == 19
    assert {"Reset", "SetChargingProfile", "DataTransfer"} <= CHARGER_BOUND_ACTIONS
    assert not CHARGER_BOUND_ACTIONS & {"Heartbeat", "BootNotification", "StartTransaction", "Nope"}
