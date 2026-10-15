import re
import statistics
import time
from pathlib import Path

from ocpp_2w_proxy.monitor import recorder
from ocpp_2w_proxy.monitor.events import EventKind, FlowEvent
from ocpp_2w_proxy.monitor.recorder_hub import RecorderHub
from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.reasons import Reason

CALL = Call("w1", "Heartbeat", {})

# The relay-path rules of docs/plans/flow-monitor.md, "Performance budget".
FORBIDDEN = {
    "json": r"\bjson\b",
    "copy": r"\bcopy\b",
    "uuid": r"\buuid\b",
    "await": r"\bawait\b",
    "f-string": r"\bf[\"']",
    "len()": r"\blen\(",
}


def test_recorder_module_has_nothing_expensive_on_the_relay_path():
    source = Path(recorder.__file__).read_text()

    found = [name for name, pattern in FORBIDDEN.items() if re.search(pattern, source)]

    assert found == []


def test_each_hook_enqueues_one_tuple_in_flow_event_field_order():
    hub = RecorderHub(["cp"])
    rec = hub.recorder_for("cp")
    reply = CallResult("w1", {})

    rec.received("charger", CALL)
    rec.sent("primary", CALL)
    rec.decided("stats", CALL, Reason.QUEUED, 5)
    rec.link_changed("primary", False)
    rec.received("primary", reply)

    raw = [hub.queue.get_nowait() for _ in range(5)]
    assert all(type(item) is tuple for item in raw)
    events = [FlowEvent._make(item) for item in raw]
    assert [(e.kind, e.charger_id, e.peer, e.message, e.reason, e.detail) for e in events] == [
        (EventKind.RECEIVED, "cp", "charger", CALL, None, None),
        (EventKind.SENT, "cp", "primary", CALL, None, None),
        (EventKind.DECIDED, "cp", "stats", CALL, Reason.QUEUED, 5),
        (EventKind.LINK, "cp", "primary", None, None, False),
        (EventKind.RECEIVED, "cp", "primary", reply, None, None),
    ]
    assert all(type(e.at) is float and abs(e.at - time.time()) < 5 for e in events)


def test_queue_is_shared_and_sized_per_charger():
    hub = RecorderHub(["a", "b", "c"], per_charger=4)

    hub.recorder_for("a").sent("primary", CALL)
    hub.recorder_for("c").sent("primary", CALL)

    assert hub.queue.maxsize == 12
    assert [hub.queue.get_nowait()[2] for _ in range(2)] == ["a", "c"]


def test_a_full_queue_drops_and_counts_per_charger():
    hub = RecorderHub(["a", "b"], per_charger=1)
    hub.recorder_for("a").sent("primary", CALL)
    hub.recorder_for("a").sent("primary", CALL)

    hub.recorder_for("b").sent("primary", CALL)
    hub.recorder_for("b").link_changed("primary", True)
    hub.recorder_for("b").decided("primary", CALL, Reason.QUEUED)

    assert hub.queue.qsize() == 2
    assert hub.dropped() == {"a": 0, "b": 3}
    assert set(hub.last_drop_at()) == {"b"}
    assert abs(hub.last_drop_at()["b"] - time.time()) < 5


def test_hook_costs_about_a_microsecond():
    calls, chunk = 100_000, 1_000
    hub = RecorderHub(["cp"], per_charger=calls)
    sent = hub.recorder_for("cp").sent
    costs = []
    for _ in range(calls // chunk):
        start = time.perf_counter()
        for _ in range(chunk):
            sent("primary", CALL)
        costs.append((time.perf_counter() - start) / chunk)

    assert hub.dropped() == {"cp": 0}
    # About 1 µs on a quiet machine; generous for a shared CI runner.
    assert statistics.median(costs) <= 2.5e-6, f"median hook cost {statistics.median(costs) * 1e6:.2f} µs"
