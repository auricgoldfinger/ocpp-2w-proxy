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


def test_parses_defaults_and_secrets():
    config = parse(raw(), {"TAP": "pw"})
    assert config.primary.url == "wss://primary.example/ocpp"
    assert config.primary.auth is AuthMode.NONE
    assert config.secondaries[0].password == "pw"
    assert config.secondaries[0].policy.rule_for("Reset") is Rule.ANSWER
    assert config.secondaries[0].policy.rule_for("RemoteStartTransaction") is Rule.ANSWER
    assert not config.secondaries[0].policy.strip_charging_profile
    assert "MeterValues" in config.secondaries[0].forward_actions


def test_missing_secret_env_is_an_error():
    with pytest.raises(ConfigError, match="TAP"):
        parse(raw(), {})


def test_secondary_forward_auth_refused():
    with pytest.raises(ConfigError, match="leak"):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "auth": "forward"}]), {})


def test_policy_overrides_are_validated():
    with pytest.raises(ConfigError):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {"Foo": "answer"}}}]), {})
    with pytest.raises(ConfigError):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {"Reset": "maybe"}}}]), {})


def test_exclusive_command_moved_to_secondary():
    config = parse(
        raw(
            primary={"url": "wss://p", "policy": {"actions": {"Reset": "answer"}}},
            secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {"Reset": "forward"}}}],
        ),
        {},
    )
    assert config.primary.policy.rule_for("Reset") is Rule.ANSWER
    assert config.secondaries[0].policy.rule_for("Reset") is Rule.FORWARD


def test_conflicting_exclusive_command_is_rejected():
    with pytest.raises(ConfigError, match="exclusive command Reset is forwarded by more than one backend: primary"):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {"Reset": "forward"}}}]), {})


def test_exclusive_command_forwarded_by_two_secondaries_is_rejected():
    raw_config = raw(
        primary={"url": "wss://p", "policy": {"actions": {"Reset": "answer"}}},
        secondary=[
            {"name": "tap", "url": "wss://x", "policy": {"actions": {"Reset": "forward"}}},
            {"name": "stats", "url": "wss://y", "policy": {"actions": {"Reset": "forward"}}},
        ],
    )
    with pytest.raises(ConfigError, match="exclusive command Reset.*tap, stats"):
        parse(raw_config, {})


@pytest.mark.parametrize(
    "action",
    ["RemoteStartTransaction", "SendLocalList", "ReserveNow", "CancelReservation"],
)
def test_authorization_command_stays_with_primary(action):
    with pytest.raises(ConfigError, match=f"authorization command {action}"):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {action: "forward"}}}]), {})


def test_secondary_forwarding_all_configuration_changes_is_rejected():
    with pytest.raises(ConfigError, match="may not forward all configuration changes"):
        parse(
            raw(
                secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {"ChangeConfiguration": "forward"}}}]
            ),
            {},
        )


def test_secondary_authorization_settings_key_is_rejected():
    bad = raw(
        primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
        secondary=[
            {"name": "tap", "url": "wss://x", "policy": {"change_configuration_allow_keys": ["LocalAuthListEnabled"]}}
        ],
    )
    with pytest.raises(ConfigError, match="localauthlistenabled"):
        parse(bad, {})


def test_authorization_settings_key_is_rejected_in_any_case():
    bad = raw(
        primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
        secondary=[
            {"name": "tap", "url": "wss://x", "policy": {"change_configuration_allow_keys": ["LOCALAUTHLISTENABLED"]}}
        ],
    )
    with pytest.raises(ConfigError, match="localauthlistenabled"):
        parse(bad, {})


@pytest.mark.parametrize(
    "key",
    ["AuthorizeRemoteTxRequests", "AuthorizationKey", "SecurityProfile", "MaxEnergyOnInvalidId"],
)
def test_authorization_and_security_keys_stay_with_primary(key):
    bad = raw(
        primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
        secondary=[{"name": "tap", "url": "wss://x", "policy": {"change_configuration_allow_keys": [key]}}],
    )
    with pytest.raises(ConfigError, match=key.lower()):
        parse(bad, {})


def test_secondary_owned_key_is_withheld_from_a_primary_that_forwards_all_changes():
    config = parse(
        raw(
            secondary=[
                {
                    "name": "tap",
                    "url": "wss://x",
                    "policy": {"change_configuration_allow_keys": ["MeterValueSampleInterval"]},
                }
            ]
        ),
        {},
    )
    assert config.primary.policy.withheld_configuration_keys == frozenset({"metervaluesampleinterval"})
    assert config.primary.policy.rule_for("ChangeConfiguration") is Rule.FORWARD


def test_configuration_key_permitted_for_one_backend_only():
    raw_config = raw(
        primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
        secondary=[
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
    )
    with pytest.raises(ConfigError, match="configuration key 'metervaluesampleinterval' is permitted for both"):
        parse(raw_config, {})


def test_configuration_key_permitted_for_one_backend():
    config = parse(
        raw(
            primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
            secondary=[
                {
                    "name": "tap",
                    "url": "wss://x",
                    "policy": {"change_configuration_allow_keys": ["MeterValueSampleInterval"]},
                }
            ],
        ),
        {},
    )
    assert config.secondaries[0].policy.change_configuration_allow_keys == frozenset({"metervaluesampleinterval"})


def test_invalid_log_level_is_rejected():
    with pytest.raises(ConfigError, match="BOGUS"):
        parse(raw(logging={"level": "BOGUS"}), {"TAP": "pw"})


@pytest.mark.parametrize(
    "where",
    [
        {"chargers": [{"id": "CH1", "password": "secret"}]},
        {"primary": {"url": "wss://p", "password": "secret"}},
        {"secondary": [{"name": "tap", "url": "wss://x", "auth": "basic", "password": "secret"}]},
    ],
)
def test_password_in_config_file_is_rejected(where):
    with pytest.raises(ConfigError, match="password_env"):
        parse(raw(**where), {"TAP": "pw"})


@pytest.mark.parametrize(
    "bad",
    [
        {"chargers": []},
        {"chargers": [{"id": "bad id"}]},
        {"chargers": [{"id": "A"}, {"id": "A"}]},
        {"primary": {"url": "http://x"}},
        {"primary": {"url": "wss://x", "auth": "basic"}},
        {"proxy": {"tls_cert": "/c"}},
        {"secondary": {"name": "tap", "url": "wss://x"}},  # a table, not [[secondary]] entries
        {"secondary": [{"url": "wss://x"}]},  # missing name
        {"secondary": [{"name": "bad name", "url": "wss://x"}]},
        {"secondary": [{"name": "tap", "url": "wss://x"}, {"name": "tap", "url": "wss://y"}]},
    ],
)
def test_invalid_configs(bad):
    with pytest.raises(ConfigError):
        parse(raw(**bad), {"TAP": "pw"})


def test_chargers_as_a_table_is_rejected():
    with pytest.raises(ConfigError, match="array-of-tables"):
        parse(raw(chargers={"id": "CH1"}), {"TAP": "pw"})


@pytest.mark.parametrize(
    "where",
    [
        {"proxy": {"unknown_key": 1}},
        {"logging": {"unknown_key": True}},
        {"chargers": [{"id": "CH1", "unknown_key": 1}]},
        {"primary": {"url": "wss://p", "unknown_key": 1}},
        {"secondary": [{"name": "tap", "url": "wss://x", "auth": "basic", "password_env": "TAP", "unknown_key": 1}]},
        {
            "secondary": [
                {"name": "tap", "url": "wss://x", "auth": "basic", "password_env": "TAP", "policy": {"unknown_key": 1}}
            ]
        },
    ],
)
def test_unknown_keys_are_rejected(where):
    with pytest.raises(ConfigError, match="unknown key"):
        parse(raw(**where), {"TAP": "pw"})


@pytest.mark.parametrize(
    "bad",
    [
        {"proxy": {"port": "not a number"}},
        {"proxy": {"ping_interval": "soon"}},
        {"primary": {"url": "wss://p", "call_timeout": "soon"}},
        {"secondary": [{"name": "tap", "url": "wss://x", "auth": "basic", "password_env": "TAP", "max_queue": "lots"}]},
    ],
)
def test_bad_numbers_are_config_errors(bad):
    with pytest.raises(ConfigError, match="must be a number"):
        parse(raw(**bad), {"TAP": "pw"})


@pytest.mark.parametrize(
    "bad",
    [
        {"proxy": {"port": 70000}},
        {"proxy": {"port": -1}},
        {"proxy": {"ping_interval": 0}},
        {"primary": {"url": "wss://p", "call_timeout": 0}},
        {"primary": {"url": "wss://p", "max_queue": 0}},
        {"primary": {"url": "wss://p", "max_queue": 9_999}},
        {"secondary": [{"name": "tap", "url": "wss://x", "auth": "basic", "password_env": "TAP", "max_queue": -5}]},
    ],
)
def test_out_of_range_numbers_are_config_errors(bad):
    with pytest.raises(ConfigError, match="must be (between|at least)"):
        parse(raw(**bad), {"TAP": "pw"})


@pytest.mark.parametrize(
    "bad",
    [
        {"logging": {"log_payloads": "false"}},  # a non-empty string would have meant True
        {"primary": {"url": "wss://p", "policy": {"strip_charging_profile": "no"}}},
    ],
)
def test_flags_must_be_real_booleans(bad):
    with pytest.raises(ConfigError, match="must be true or false"):
        parse(raw(**bad), {"TAP": "pw"})


def test_charger_entry_that_is_not_a_table_is_a_config_error():
    with pytest.raises(ConfigError, match="must be tables"):
        parse(raw(chargers=["CH1"]), {"TAP": "pw"})


def test_misspelled_policy_action_is_rejected():
    with pytest.raises(ConfigError, match="unknown action"):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "policy": {"actions": {"Resett": "answer"}}}]), {})


def test_misspelled_forward_action_is_rejected():
    with pytest.raises(ConfigError, match="unknown action"):
        parse(raw(secondary=[{"name": "tap", "url": "wss://x", "forward_actions": ["MeterValuess"]}]), {})


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


def test_charger_secondary_id_for_unknown_backend_is_rejected():
    bad = raw(
        chargers=[{"id": "CH1", "secondary_ids": {"nope": "X"}}],
        secondary=[{"name": "tap", "url": "wss://x"}],
    )
    with pytest.raises(ConfigError, match="nope"):
        parse(bad, {})


def test_example_config_is_valid():
    example = Path(__file__).resolve().parent.parent / "config.example.toml"
    config = parse(tomllib.loads(example.read_text()), {})
    assert config.primary.name == "primary"
    assert [backend.name for backend in config.secondaries] == ["solaredge"]
    # The worked example of BR-009: the profiles are taken away from the primary ...
    assert config.primary.policy.rule_for("SetChargingProfile") is Rule.ANSWER
    # ... and assigned to exactly one secondary backend.
    assert config.secondaries[0].policy.rule_for("SetChargingProfile") is Rule.FORWARD


def test_secondary_may_not_take_the_primary_name():
    with pytest.raises(ConfigError, match="reserved for the primary"):
        parse(raw(secondary=[{"name": "primary", "url": "wss://x"}]), {})


def test_queue_defaults_to_the_minimum_capacity():
    config = parse(raw(), {"TAP": "pw"})
    assert config.primary.max_queue == 10_000
    assert config.secondaries[0].max_queue == 10_000
