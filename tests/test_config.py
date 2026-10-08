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


def test_policy_overrides_and_validation():
    config = parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"Reset": "forward"}}}), {})
    assert config.secondary.policy.rule_for("Reset") is Rule.FORWARD
    with pytest.raises(ConfigError):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"Foo": "answer"}}}), {})
    with pytest.raises(ConfigError):
        parse(raw(secondary={"url": "wss://x", "policy": {"actions": {"Reset": "maybe"}}}), {})


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
