from pathlib import Path

import pytest
from test_config import raw

from ocpp_2w_proxy.config import ConfigError, parse
from ocpp_2w_proxy.monitor_config import MonitorConfig


def test_monitor_is_off_by_default_bound_to_loopback_and_stored_in_the_state_dir():
    config = parse(raw(proxy={"state_dir": "/var/lib/proxy"}), {"TAP": "pw"})
    assert config.monitor == MonitorConfig(db_path=Path("/var/lib/proxy/monitor.sqlite"))
    assert not config.monitor.enabled
    assert config.monitor.listen == "127.0.0.1"
    assert config.monitor.port == 8323
    assert config.monitor.retention_days == 90


def test_monitor_settings_are_read():
    monitor = {"enabled": True, "listen": "0.0.0.0", "port": 9000, "db_path": "/data/m.sqlite", "retention_days": 7}
    assert parse(raw(monitor=monitor), {"TAP": "pw"}).monitor == MonitorConfig(
        True, "0.0.0.0", 9000, Path("/data/m.sqlite"), 7
    )


def test_port_zero_never_clashes():
    config = parse(raw(monitor={"port": 0}, debug={"port": 0}, proxy={"port": 0}), {"TAP": "pw"})
    assert config.monitor.port == 0


@pytest.mark.parametrize(
    ("monitor", "message"),
    [
        ({"enabled": "yes"}, "enabled must be true or false"),
        ({"port": 70000}, "port must be between"),
        ({"retention_days": 0}, "retention_days must be at least"),
        ({"retention_days": "long"}, "retention_days must be a number"),
        ({"db_path": ""}, "db_path must be a file path"),
        ({"db_path": 3}, "db_path must be a file path"),
        ({"prot": 1}, "unknown key"),
        ({"port": 8321}, "charger-facing"),
        ({"port": 8400}, r"is the \[debug\] port"),
    ],
)
def test_invalid_monitor_section_is_refused(monitor, message):
    with pytest.raises(ConfigError, match=message):
        parse(raw(monitor=monitor, proxy={"port": 8321}, debug={"port": 8400}), {"TAP": "pw"})


def test_monitor_must_be_a_table():
    with pytest.raises(ConfigError, match=r"\[monitor\] must be a table"):
        parse(raw(monitor=[]), {"TAP": "pw"})
