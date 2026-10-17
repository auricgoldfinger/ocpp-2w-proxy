import tomllib
from pathlib import Path

import pytest

from ocpp_2w_proxy.config import AuthMode, ConfigError, parse
from ocpp_2w_proxy.policy import Rule


def raw(**sections):
    base = {
        "chargers": [{"id": "CH1"}],
        "primary": {"url": "wss://primary.example/ocpp/"},
        "secondary": [{"name": "tap", "url": "wss://secondary.example", "auth": "basic", "password_env": "TAP"}],
    }
    base.update(sections)
    return base


def sec(**entry):
    """A single secondary named tap with the given settings."""
    return {"secondary": [{"name": "tap", "url": "wss://x", **entry}]}


def forwards(action):
    return {"actions": {action: "forward"}}


def primary_answers(action):
    return {"url": "wss://p", "policy": {"actions": {action: "answer"}}}


def allow_keys(*keys):
    """A primary answering ChangeConfiguration and tap permitted to change the given keys."""
    return {
        "primary": primary_answers("ChangeConfiguration"),
        **sec(policy={"change_configuration_allow_keys": list(keys)}),
    }


def test_parses_defaults_and_secrets():
    config = parse(raw(), {"TAP": "pw"})
    assert config.primary.url == "wss://primary.example/ocpp"
    assert config.primary.auth is AuthMode.NONE
    assert config.primary.max_queue == 10_000  # the minimum capacity
    assert config.primary.boot_replay_grace == 0
    assert config.primary.outage_grace == 30
    assert config.secondaries[0].password == "pw"
    assert config.secondaries[0].max_queue == 10_000
    assert config.secondaries[0].policy.rule_for("Reset") is Rule.ANSWER
    assert config.secondaries[0].policy.rule_for("RemoteStartTransaction") is Rule.ANSWER
    assert not config.secondaries[0].policy.strip_charging_profile
    assert not config.secondaries[0].policy.resume_own_stops
    assert "MeterValues" in config.secondaries[0].forward_actions


@pytest.mark.parametrize(
    ("where", "read", "value"),
    [
        ({"primary": {"url": "wss://p", "boot_replay_grace": 60}}, lambda c: c.primary.boot_replay_grace, 60),
        ({"primary": {"url": "wss://p", "outage_grace": 0}}, lambda c: c.primary.outage_grace, 0),
        (sec(policy={"resume_own_stops": True}), lambda c: c.secondaries[0].policy.resume_own_stops, True),
    ],
    ids=["boot_replay_grace", "outage_grace", "resume_own_stops"],
)
def test_settings_can_be_set(where, read, value):
    assert read(parse(raw(**where), {"TAP": "pw"})) == value


def test_missing_secret_env_is_an_error():
    with pytest.raises(ConfigError, match="TAP"):
        parse(raw(), {})


AUTHORIZATION_COMMANDS = ["RemoteStartTransaction", "SendLocalList", "ReserveNow", "CancelReservation"]
AUTHORIZATION_KEYS = ["AuthorizeRemoteTxRequests", "AuthorizationKey", "SecurityProfile", "MaxEnergyOnInvalidId"]

REJECTED = [
    pytest.param(sec(auth="forward"), "leak", id="secondary-forward-auth"),
    pytest.param(sec(policy={"actions": {"Reset": "maybe"}}), "'maybe' is not a valid Rule", id="unknown-rule"),
    pytest.param(sec(policy={"actions": {"Resett": "answer"}}), "unknown action", id="misspelled-policy-action"),
    pytest.param(sec(forward_actions=["MeterValuess"]), "unknown action", id="misspelled-forward-action"),
    pytest.param(
        sec(policy=forwards("Reset")),
        "exclusive command Reset is forwarded by more than one backend: primary",
        id="exclusive-command-forwarded-by-primary-and-secondary",
    ),
    pytest.param(
        {
            "primary": primary_answers("Reset"),
            "secondary": [
                {"name": "tap", "url": "wss://x", "policy": forwards("Reset")},
                {"name": "stats", "url": "wss://y", "policy": forwards("Reset")},
            ],
        },
        "exclusive command Reset.*tap, stats",
        id="exclusive-command-forwarded-by-two-secondaries",
    ),
    *[
        pytest.param(sec(policy=forwards(action)), f"authorization command {action}", id=f"secondary-forwards-{action}")
        for action in AUTHORIZATION_COMMANDS
    ],
    pytest.param(
        sec(policy=forwards("ChangeConfiguration")),
        "may not forward all configuration changes",
        id="secondary-forwards-all-configuration-changes",
    ),
    pytest.param(allow_keys("LocalAuthListEnabled"), "localauthlistenabled", id="secondary-key-LocalAuthListEnabled"),
    pytest.param(allow_keys("LOCALAUTHLISTENABLED"), "localauthlistenabled", id="secondary-key-in-any-case"),
    *[pytest.param(allow_keys(key), key.lower(), id=f"secondary-key-{key}") for key in AUTHORIZATION_KEYS],
    pytest.param(
        {
            "primary": primary_answers("ChangeConfiguration"),
            "secondary": [
                {
                    "name": "tap",
                    "url": "wss://x",
                    "policy": {"change_configuration_allow_keys": ["MeterValueSampleInterval"]},
                },
                {
                    "name": "stats",
                    "url": "wss://y",
                    "policy": {"change_configuration_allow_keys": ["meterValueSampleInterval", "HeartbeatInterval"]},
                },
            ],
        },
        "configuration key 'metervaluesampleinterval' is permitted for both",
        id="configuration-key-permitted-for-two-backends",
    ),
    pytest.param(
        {"primary": {"url": "wss://p", "policy": {"resume_own_stops": True}}},
        "secondary backends only",
        id="primary-resume-own-stops",
    ),
    pytest.param({"logging": {"level": "BOGUS"}}, "BOGUS", id="log-level"),
    pytest.param({"chargers": [{"id": "CH1", "password": "secret"}]}, "password_env", id="charger-password"),
    pytest.param({"primary": {"url": "wss://p", "password": "secret"}}, "password_env", id="primary-password"),
    pytest.param(sec(auth="basic", password="secret"), "password_env", id="secondary-password"),
    pytest.param({"chargers": []}, "at least one", id="no-chargers"),
    pytest.param({"chargers": [{"id": "bad id"}]}, "'bad id' must match", id="charger-id"),
    pytest.param({"chargers": [{"id": "A"}, {"id": "A"}]}, "duplicate charger id 'A'", id="duplicate-charger"),
    pytest.param({"chargers": {"id": "CH1"}}, "array-of-tables", id="chargers-table"),
    pytest.param({"chargers": ["CH1"]}, "must be tables", id="charger-not-a-table"),
    pytest.param(
        {"chargers": [{"id": "CH1", "secondary_ids": {"nope": "X"}}], **sec()},
        "nope",
        id="secondary-id-for-unknown-backend",
    ),
    pytest.param({"primary": {"url": "http://x"}}, "url must start with ws:// or wss://", id="primary-url"),
    pytest.param(
        {"primary": {"url": "wss://x", "auth": "basic"}},
        "auth = 'basic' requires password_env",
        id="primary-basic-without-secret",
    ),
    pytest.param({"proxy": {"tls_cert": "/c"}}, "tls_cert and tls_key must be set together", id="tls-cert-alone"),
    pytest.param(
        {"secondary": {"name": "tap", "url": "wss://x"}},
        r"\[secondary\] entries are \[\[secondary\]\]",
        id="secondary-table",
    ),
    pytest.param({"secondary": [{"url": "wss://x"}]}, "name: None must match", id="secondary-without-name"),
    pytest.param(sec(name="bad name"), "'bad name' must match", id="secondary-name"),
    pytest.param(sec(name="primary"), "reserved for the primary", id="secondary-named-primary"),
    pytest.param(
        {"secondary": [{"name": "tap", "url": "wss://x"}, {"name": "tap", "url": "wss://y"}]},
        "duplicate secondary backend name 'tap'",
        id="duplicate-secondary",
    ),
    *[
        pytest.param(where, "unknown key", id=f"unknown-key-{name}")
        for name, where in [
            ("proxy", {"proxy": {"unknown_key": 1}}),
            ("logging", {"logging": {"unknown_key": True}}),
            ("charger", {"chargers": [{"id": "CH1", "unknown_key": 1}]}),
            ("primary", {"primary": {"url": "wss://p", "unknown_key": 1}}),
            ("secondary", sec(unknown_key=1)),
            ("secondary-outage_grace", sec(outage_grace=5)),  # only the primary's outage is mirrored to the charger
            ("secondary-policy", sec(policy={"unknown_key": 1})),
        ]
    ],
    *[
        pytest.param(where, "must be a number", id=f"not-a-number-{name}")
        for name, where in [
            ("port", {"proxy": {"port": "not a number"}}),
            ("ping_interval", {"proxy": {"ping_interval": "soon"}}),
            ("call_timeout", {"primary": {"url": "wss://p", "call_timeout": "soon"}}),
            ("outage_grace", {"primary": {"url": "wss://p", "outage_grace": "soon"}}),
            ("max_queue", sec(max_queue="lots")),
        ]
    ],
    *[
        pytest.param(where, "must be (between|at least)", id=f"out-of-range-{name}")
        for name, where in [
            ("port-high", {"proxy": {"port": 70000}}),
            ("port-negative", {"proxy": {"port": -1}}),
            ("ping_interval", {"proxy": {"ping_interval": 0}}),
            ("call_timeout", {"primary": {"url": "wss://p", "call_timeout": 0}}),
            ("max_queue-zero", {"primary": {"url": "wss://p", "max_queue": 0}}),
            ("max_queue-below-minimum", {"primary": {"url": "wss://p", "max_queue": 9_999}}),
            ("outage_grace", {"primary": {"url": "wss://p", "outage_grace": -1}}),
            ("boot_replay_grace", {"primary": {"url": "wss://p", "boot_replay_grace": -1}}),
            ("secondary-max_queue", sec(max_queue=-5)),
        ]
    ],
    pytest.param(
        {"logging": {"log_payloads": "false"}}, "must be true or false", id="string-flag"
    ),  # "false" would be True
    pytest.param(
        {"primary": {"url": "wss://p", "policy": {"strip_charging_profile": "no"}}},
        "must be true or false",
        id="string-policy-flag",
    ),
]


@pytest.mark.parametrize(("where", "match"), REJECTED)
def test_rejected(where, match):
    with pytest.raises(ConfigError, match=match):
        parse(raw(**where), {"TAP": "pw"})


def test_exclusive_command_moved_to_secondary():
    config = parse(raw(primary=primary_answers("Reset"), **sec(policy=forwards("Reset"))), {})
    assert config.primary.policy.rule_for("Reset") is Rule.ANSWER
    assert config.secondaries[0].policy.rule_for("Reset") is Rule.FORWARD


def test_secondary_owned_key_is_withheld_from_a_primary_that_forwards_all_changes():
    config = parse(raw(**sec(policy={"change_configuration_allow_keys": ["MeterValueSampleInterval"]})), {})
    assert config.primary.policy.withheld_configuration_keys == frozenset({"metervaluesampleinterval"})
    assert config.primary.policy.rule_for("ChangeConfiguration") is Rule.FORWARD


def test_configuration_key_permitted_for_one_backend():
    config = parse(raw(**allow_keys("MeterValueSampleInterval")), {})
    assert config.secondaries[0].policy.change_configuration_allow_keys == frozenset({"metervaluesampleinterval"})


def test_secondary_is_optional():
    config = raw()
    del config["secondary"]
    assert parse(config, {}).secondaries == ()


def test_five_named_secondaries_parse():
    config = raw(secondary=[{"name": name, "url": f"wss://{name}.example"} for name in ("a", "b", "c", "d", "e")])
    parsed = parse(config, {})
    assert [backend.name for backend in parsed.secondaries] == ["a", "b", "c", "d", "e"]


def test_charger_secondary_ids_per_backend():
    config = raw(
        chargers=[{"id": "CH1", "primary_id": "P1", "secondary_ids": {"tap": "TAP-1"}}],
        secondary=[{"name": "tap", "url": "wss://x"}, {"name": "stats", "url": "wss://y"}],
    )
    parsed = parse(config, {})
    assert parsed.chargers["CH1"].primary_id == "P1"
    assert parsed.chargers["CH1"].secondary_ids == {"tap": "TAP-1"}


def test_example_config_is_valid():
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    config = parse(tomllib.loads(example.read_text()), {})
    assert config.primary.name == "primary"
    assert [backend.name for backend in config.secondaries] == ["homeassistant"]
    # The worked example of BR-009: the profiles are taken away from the primary ...
    assert config.primary.policy.rule_for("SetChargingProfile") is Rule.ANSWER
    # ... and assigned to exactly one secondary backend.
    assert config.secondaries[0].policy.rule_for("SetChargingProfile") is Rule.FORWARD
