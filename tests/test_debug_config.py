import pytest
from test_config import raw

from ocpp_2w_proxy.config import ConfigError, parse
from ocpp_2w_proxy.debug_config import DebugConfig


def test_debug_is_off_by_default_and_bound_to_loopback():
    config = parse(raw(), {"TAP": "pw"})
    assert config.debug == DebugConfig()
    assert not config.debug.enabled
    assert config.debug.listen == "127.0.0.1"
    assert config.debug.port == 8322


def test_debug_settings_are_read():
    debug = {"enabled": True, "listen": "0.0.0.0", "port": 9000, "default_timeout": 5, "max_timeout": 60}
    assert parse(raw(debug=debug), {"TAP": "pw"}).debug == DebugConfig(True, "0.0.0.0", 9000, 5, 60)


@pytest.mark.parametrize(
    ("debug", "message"),
    [
        ({"enabled": "yes"}, "enabled must be true or false"),
        ({"port": 70000}, "port must be between"),
        ({"max_timeout": 0}, "max_timeout must be at least"),
        ({"default_timeout": 100, "max_timeout": 50}, "default_timeout must be between"),
        ({"prot": 1}, "unknown key"),
        ({"port": 8321}, "charger-facing"),
    ],
)
def test_invalid_debug_section_is_refused(debug, message):
    with pytest.raises(ConfigError, match=message):
        parse(raw(debug=debug, proxy={"port": 8321}), {"TAP": "pw"})


def test_debug_must_be_a_table():
    with pytest.raises(ConfigError, match=r"\[debug\] must be a table"):
        parse(raw(debug=[]), {"TAP": "pw"})
