import pytest

from ocpp_2w_proxy.config import AuthMode, ConfigError, parse
from ocpp_2w_proxy.policy import Rule


def raw(**sections):
    base = {
        "chargers": [{"id": "CH1"}],
        "primary": {"url": "wss://primary.example/ocpp/"},
        "secondary": {"url": "wss://secondary.example", "auth": "basic", "password_env": "TAP"},
    }
    base.update(sections)
    return base


def test_parses_defaults_and_secrets():
    config = parse(raw(), {"TAP": "pw"})
    assert config.primary.url == "wss://primary.example/ocpp"
    assert config.primary.auth is AuthMode.NONE
    assert config.secondary.password == "pw"
    assert config.secondary.policy.rule_for("Reset") is Rule.ANSWER
    assert config.secondary.policy.rule_for("RemoteStartTransaction") is Rule.ANSWER
    assert not config.secondary.policy.strip_charging_profile
    assert "MeterValues" in config.secondary.forward_actions


def test_missing_secret_env_is_an_error():
    with pytest.raises(ConfigError, match="TAP"):
        parse(raw(), {})


def test_secondary_forward_auth_refused():
    with pytest.raises(ConfigError, match="leak"):
        parse(raw(secondary={"url": "wss://x", "auth": "forward"}), {})


def test_policy_overrides_are_validated():
    with pytest.raises(ConfigError):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"Foo": "answer"}}}), {})
    with pytest.raises(ConfigError):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"Reset": "maybe"}}}), {})


def test_exclusive_command_moved_to_secondary():
    config = parse(
        raw(
            primary={"url": "wss://p", "policy": {"actions": {"Reset": "answer"}}},
            secondary={"url": "wss://x", "policy": {"actions": {"Reset": "forward"}}},
        ),
        {},
    )
    assert config.primary.policy.rule_for("Reset") is Rule.ANSWER
    assert config.secondary.policy.rule_for("Reset") is Rule.FORWARD


def test_conflicting_exclusive_command_is_rejected():
    with pytest.raises(ConfigError, match="exclusive command Reset is forwarded by more than one backend: primary"):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"Reset": "forward"}}}), {})


@pytest.mark.parametrize(
    "action",
    ["RemoteStartTransaction", "SendLocalList", "ReserveNow", "CancelReservation"],
)
def test_authorization_command_stays_with_primary(action):
    with pytest.raises(ConfigError, match=f"authorization command {action}"):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {action: "forward"}}}), {})


def test_secondary_forwarding_all_configuration_changes_is_rejected():
    with pytest.raises(ConfigError, match="may not forward all configuration changes"):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"ChangeConfiguration": "forward"}}}), {})


def test_secondary_authorization_settings_key_is_rejected():
    bad = raw(
        primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
        secondary={"url": "wss://x", "policy": {"change_configuration_allow_keys": ["LocalAuthListEnabled"]}},
    )
    with pytest.raises(ConfigError, match="LocalAuthListEnabled"):
        parse(bad, {})


def test_configuration_key_blocked_while_primary_forwards_all_changes():
    bad = raw(secondary={"url": "wss://x", "policy": {"change_configuration_allow_keys": ["MeterValueSampleInterval"]}})
    with pytest.raises(ConfigError, match="MeterValueSampleInterval.*forwards all configuration changes"):
        parse(bad, {})


def test_configuration_key_permitted_for_one_backend():
    config = parse(
        raw(
            primary={"url": "wss://p", "policy": {"actions": {"ChangeConfiguration": "answer"}}},
            secondary={"url": "wss://x", "policy": {"change_configuration_allow_keys": ["MeterValueSampleInterval"]}},
        ),
        {},
    )
    assert config.secondary.policy.change_configuration_allow_keys == frozenset({"MeterValueSampleInterval"})


def test_invalid_log_level_is_rejected():
    with pytest.raises(ConfigError, match="BOGUS"):
        parse(raw(logging={"level": "BOGUS"}), {"TAP": "pw"})


@pytest.mark.parametrize(
    "where",
    [
        {"chargers": [{"id": "CH1", "password": "secret"}]},
        {"primary": {"url": "wss://p", "password": "secret"}},
        {"secondary": {"url": "wss://x", "auth": "basic", "password": "secret"}},
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
    ],
)
def test_invalid_configs(bad):
    with pytest.raises(ConfigError):
        parse(raw(**bad), {"TAP": "pw"})


def test_secondary_is_optional():
    config = raw()
    del config["secondary"]
    assert parse(config, {}).secondary is None
