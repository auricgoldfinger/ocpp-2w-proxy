import pytest

from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.monitor.not_sent import NotSentMap

RAW = {
    "chargers": [
        {"id": "CH1", "secondary_ids": {"ha": "ha-1", "stats": "st-1"}},
        {"id": "CH2", "secondary_ids": {"stats": "st-2"}},
    ],
    "primary": {"url": "wss://primary.example"},
    "secondary": [
        {"name": "ha", "url": "wss://ha.example", "forward_actions": ["MeterValues", "StatusNotification"]},
        {"name": "stats", "url": "wss://stats.example", "forward_actions": ["MeterValues"]},
        {"name": "unused", "url": "wss://unused.example", "forward_actions": []},
    ],
}


@pytest.fixture(scope="module")
def not_sent():
    return NotSentMap.from_config(parse(RAW, {}))


@pytest.mark.parametrize(
    ("charger", "origin", "action", "expected"),
    [
        ("CH1", "charger", "MeterValues", ()),
        ("CH1", "charger", "StatusNotification", ("stats",)),
        ("CH1", "charger", "Heartbeat", ("ha", "stats")),
        ("CH2", "charger", "StatusNotification", ("stats",)),
        ("CH2", "charger", "MeterValues", ()),
        ("CH1", "charger", "NotAnAction", ("ha", "stats")),
        ("unknown", "charger", "Heartbeat", ()),
        # only charger-originated exchanges have a not_sent list
        ("CH1", "primary", "Heartbeat", ()),
        ("CH1", "ha", "Reset", ()),
        ("CH1", "proxy", "Heartbeat", ()),
        ("CH1", "debug", "Heartbeat", ()),
    ],
)
def test_for_exchange(not_sent, charger, origin, action, expected):
    assert not_sent.for_exchange(charger, origin, action) == expected
