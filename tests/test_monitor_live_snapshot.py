"""The live snapshot of /api/links, read from the channels and the monitor counters (docs/monitor_api.md)."""

from types import SimpleNamespace

from fakes import RecordingFlow

from ocpp_2w_proxy.flow import FanOut
from ocpp_2w_proxy.monitor.live_snapshot import LiveSnapshot
from ocpp_2w_proxy.monitor.message_rates import MessageRates
from ocpp_2w_proxy.monitor.recorder_hub import RecorderHub
from ocpp_2w_proxy.ocpp import Call

NOW = 1_000_000 * 60.0


class RaisingFlow(RecordingFlow):
    def sent(self, peer, message):
        raise RuntimeError("monitor broke")


def link(connected, since, queued):
    return SimpleNamespace(connected=connected, connected_since=since, queued_count=queued)


class FakeProxy:
    def __init__(self, contexts, connected_since):
        self._contexts = contexts
        self._connected_since = connected_since

    def charger_ids(self):
        return tuple(self._contexts)

    def charger_connected_since(self, charger_id):
        return self._connected_since.get(charger_id)

    def context_for(self, charger_id):
        return self._contexts[charger_id]


def make_snapshot():
    fanout = FanOut(RecordingFlow(), RaisingFlow())
    fanout.sent("tap", Call("w1", "Heartbeat", {}))
    contexts = {
        "a": SimpleNamespace(
            primary=link(True, NOW - 100, 3),
            secondaries={"tap": link(False, None, 0)},
            traffic=fanout,
        ),
        "b": SimpleNamespace(primary=link(False, None, 0), secondaries={}, traffic=RecordingFlow()),
    }
    recorders = RecorderHub(["a", "b"], per_charger=1)
    recorders.recorder_for("b").sent("x", None)
    recorders.recorder_for("b").sent("x", None)  # queue of 2 holds both; the third is dropped
    recorders.recorder_for("b").sent("x", None)
    rates = MessageRates()
    for _ in range(4):
        rates.count("a", "main", NOW - 30)
    writer = SimpleNamespace(rates=rates, dropped_batches=2)
    proxy = FakeProxy(contexts, {"a": NOW - 200})
    return LiveSnapshot(proxy, "main", recorders, writer, clock=lambda: NOW)


def test_reports_each_charger_with_its_peers_and_monitor_counters():
    snapshot = make_snapshot()()

    a, b = snapshot["chargers"]
    assert a == {
        "id": "a",
        "connected": True,
        "since": NOW - 200,
        "peers": [
            {"name": "main", "role": "primary", "up": True, "since": NOW - 100, "queued": 3, "messages_last_hour": 4},
            {"name": "tap", "role": "secondary", "up": False, "since": None, "queued": 0, "messages_last_hour": 0},
        ],
        "monitor": {"dropped": 0, "last_drop_at": None},
    }
    assert (b["connected"], b["since"], b["monitor"]["dropped"]) == (False, None, 1)
    assert b["monitor"]["last_drop_at"] is not None
    assert snapshot["monitor"] == {"fanout_failures": 1, "dropped_batches": 2}


def test_connected_chargers_are_those_with_a_session():
    assert make_snapshot().connected_chargers() == {"a"}
