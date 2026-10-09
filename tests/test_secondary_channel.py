import asyncio
from dataclasses import replace

import pytest
from fakes import FakeCsms

from ocpp_2w_proxy.backend_link import BackendUnavailable
from ocpp_2w_proxy.backoff import Backoff
from ocpp_2w_proxy.charger_auth import ChargerIdentity
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.secondary_channel import (
    MAX_CALL_ERROR_ATTEMPTS,
    MAX_TRANSIENT_QUEUE,
    SecondaryChannel,
    _QueuedCall,
)
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog
from ocpp_2w_proxy.transactions import TransactionMap


def make_config(*secondaries):
    """max_queue below the configurable minimum is applied after parsing, so overflow
    is reached with a handful of calls."""
    config = parse(
        {
            "chargers": [{"id": "CH1"}],
            "primary": {"url": "ws://p"},
            "secondary": [
                {key: value for key, value in entry.items() if key != "max_queue"} | {"call_timeout": 1}
                for entry in secondaries
            ],
        },
        {},
    )
    queues = {entry["name"]: entry["max_queue"] for entry in secondaries if "max_queue" in entry}
    backends = tuple(replace(b, max_queue=queues[b.name]) if b.name in queues else b for b in config.secondaries)
    return replace(config, secondaries=backends)


def make_channel(config, backend_name, store):
    async def on_call(call):  # pragma: no cover
        pass

    backend = next(b for b in config.secondaries if b.name == backend_name)
    charger = config.chargers["CH1"]
    channel = SecondaryChannel(backend, charger, store, TransactionMap(store), TrafficLog("CH1", False))
    channel.attach(ChargerIdentity(charger, None, None), on_call)
    return channel


async def test_offline_keeps_only_durable_calls_and_caches_boot_and_status(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 3})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel.submit(Call("1", "BootNotification", {"chargePointVendor": "v", "chargePointModel": "m"}))
    channel.submit(Call("2", "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}))
    channel.submit(Call("3", "Heartbeat", {}))
    channel.submit(Call("4", "DataTransfer", {"vendorId": "x"}))
    channel.submit(Call("5", "StartTransaction", {"connectorId": 1}), start_ref="ref")
    store.flush()  # the coalesced queue write lands now, not in FLUSH_DELAY

    state = StateStore(tmp_path / "CH1.json").state
    assert [item["call"]["action"] for item in state.outboxes["tap"]] == ["StartTransaction"]
    assert state.outboxes["tap"][0]["start_ref"] == "ref"
    assert state.outboxes["tap"][0]["call"]["id"] != "5"  # own id, charger ids restart after reboot
    assert state.boot["chargePointVendor"] == "v"
    assert state.statuses["1"]["status"] == "Available"


async def test_each_backend_has_its_own_queue(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s"}, {"name": "stats", "url": "ws://s"})
    store = StateStore(tmp_path / "CH1.json")
    tap = make_channel(config, "tap", store)
    stats = make_channel(config, "stats", store)
    tap.submit(Call("1", "StartTransaction", {"connectorId": 1}), start_ref="a")
    stats.submit(Call("2", "StartTransaction", {"connectorId": 1}), start_ref="a")

    assert len(store.state.outboxes["tap"]) == 1
    assert len(store.state.outboxes["stats"]) == 1

    tap._complete(tap._queue[0])
    assert len(store.state.outboxes["tap"]) == 0
    assert len(store.state.outboxes["stats"]) == 1  # the other backend's queue is untouched


async def test_queue_limit_drops_meter_values_first(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 3})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel.submit(Call("1", "StartTransaction", {"connectorId": 1}))
    channel.submit(Call("2", "MeterValues", {"connectorId": 1, "meterValue": [1]}))
    channel.submit(Call("3", "MeterValues", {"connectorId": 1, "meterValue": [2]}))
    channel.submit(Call("4", "StopTransaction", {"transactionId": 1}))

    actions = [
        (item["call"]["action"], item["call"]["payload"].get("meterValue")) for item in store.state.outboxes["tap"]
    ]
    assert actions == [("StartTransaction", None), ("MeterValues", [2]), ("StopTransaction", None)]


def test_overflow_evicts_durable_items_not_transient_ones(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 1})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    heartbeat = _QueuedCall(Call("h", "Heartbeat", {}), durable=False)
    stop1 = _QueuedCall(Call("s1", "StopTransaction", {"transactionId": 1}), durable=True)
    stop2 = _QueuedCall(Call("s2", "StopTransaction", {"transactionId": 2}), durable=True)
    channel._queue.extend([heartbeat, stop1, stop2])

    channel._enforce_queue_limit()

    assert list(channel._queue) == [heartbeat, stop2]  # the oldest billing item went, the heartbeat stayed


async def test_transient_calls_are_capped_while_the_backend_refuses_them(tmp_path, monkeypatch):
    config = make_config({"name": "tap", "url": "ws://s"})
    channel = make_channel(config, "tap", StateStore(tmp_path / "CH1.json"))
    monkeypatch.setattr(SecondaryChannel, "connected", property(lambda self: True))

    for i in range(MAX_TRANSIENT_QUEUE + 5):
        channel.submit(Call(str(i), "Heartbeat", {}))
    channel.submit(Call("stop", "StopTransaction", {"transactionId": 1}))

    assert sum(not item.durable for item in channel._queue) == MAX_TRANSIENT_QUEUE
    assert sum(item.durable for item in channel._queue) == 1  # billing data is untouched


async def test_queue_restored_from_disk(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 3})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel.submit(Call("1", "StartTransaction", {"connectorId": 1}), start_ref="r")
    store.flush()  # the coalesced queue write lands now, not in FLUSH_DELAY
    restored = make_channel(config, "tap", StateStore(tmp_path / "CH1.json"))
    assert [item.call.action for item in restored._queue] == ["StartTransaction"]
    assert restored._queue[0].start_ref == "r"


async def test_boot_and_status_replay_only_for_backends_that_forward_them(tmp_path):
    config = make_config(
        {"name": "tap", "url": "ws://s"},
        {"name": "stats", "url": "ws://s", "forward_actions": ["MeterValues"]},
    )
    store = StateStore(tmp_path / "CH1.json")
    tap = make_channel(config, "tap", store)
    stats = make_channel(config, "stats", store)
    boot = Call("1", "BootNotification", {"chargePointVendor": "v", "chargePointModel": "m"})
    tap.submit(boot)
    stats.submit(boot)
    stats.submit(Call("2", "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}))

    assert tap.forwards("BootNotification")
    assert not stats.forwards("BootNotification")
    assert not stats.forwards("StatusNotification")
    # The boot/status cache is shared charger data; stats simply does not replay it.
    assert store.state.boot is not None
    assert store.state.statuses == {}


def test_rejected_stop_transaction_frees_the_transaction_link(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s"})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel._transactions.primary_started("a", 100)
    channel._transactions.secondary_started("tap", "a", 9)
    stop = _QueuedCall(Call("m", "StopTransaction", {"transactionId": 100, "meterStop": 1}), durable=True)

    channel._handle_result(stop, CallError("m", "GenericError", "no such transaction"))

    assert channel._transactions.to_secondary(100, "tap") is None


def test_overflow_never_removes_the_item_being_sent(tmp_path):
    """The sender holds the queue head while awaiting the backend's reply; an
    overflow then must drop another item, or the sender's pop afterwards would
    discard an unsent message."""
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 2})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    items = [_QueuedCall(Call(i, "MeterValues", {"connectorId": 1}), durable=True) for i in ("a", "b", "c")]
    channel._queue.extend(items)
    channel._in_flight = items[0]  # the sender is waiting for its reply right now

    channel._enforce_queue_limit()

    assert channel._queue[0] is items[0]  # the in-flight head survives
    assert items[1] not in channel._queue  # the oldest droppable meter went instead
    assert len(channel._queue) == 2


async def test_synthetic_heartbeats_at_the_requested_interval(tmp_path):
    """A backend that is not forwarded the charger's heartbeats asked for an interval
    in its BootNotification.conf: the proxy keeps it alive with synthetic ones."""
    csms = await FakeCsms(
        lambda action, payload: {"status": "Accepted", "currentTime": "2026-01-01T00:00:00Z", "interval": 1}
    ).start()
    try:
        config = make_config({"name": "tap", "url": csms.url, "forward_actions": ["BootNotification"]})
        store = StateStore(tmp_path / "CH1.json")
        store.state.boot = {"chargePointVendor": "v"}
        store.save()
        channel = SecondaryChannel(
            config.secondaries[0], config.chargers["CH1"], store, TransactionMap(store), TrafficLog("CH1", False)
        )

        async def on_call(call):  # pragma: no cover
            pass

        channel.attach(ChargerIdentity(config.chargers["CH1"], None, None), on_call)
        channel.start_background()
        try:
            await csms.wait_for_call("BootNotification")  # the cached boot is replayed
            await asyncio.wait_for(csms.wait_for_call("Heartbeat"), 5)  # within its requested 1s interval
        finally:
            await channel.close()
    finally:
        await csms.stop()


class _SilentLink:
    """A link whose Call never gets an answer."""

    async def call(self, call, timeout):
        raise TimeoutError


async def test_a_timed_out_message_stays_queued_and_forces_a_reconnect(tmp_path):
    """A silent backend must not lose billing data: the message stays at the head of the
    queue and the link is torn down (BackendUnavailable) so the worker reconnects."""
    config = make_config({"name": "tap", "url": "ws://s"})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel._transactions.primary_started("a", 100)
    channel._transactions.secondary_started("tap", "a", 9)
    stop = _QueuedCall(Call("m", "StopTransaction", {"transactionId": 100, "meterStop": 1}), durable=True)
    channel._queue.append(stop)

    for _ in range(5):  # however often it times out
        with pytest.raises(BackendUnavailable):
            await channel._send_head(_SilentLink())

    assert channel._queue[0] is stop
    assert channel._transactions.to_secondary(100, "tap") == 9  # the link to its transaction is kept


def _unattached_channel(config, store):
    return SecondaryChannel(
        config.secondaries[0], config.chargers["CH1"], store, TransactionMap(store), TrafficLog("CH1", False)
    )


async def test_idle_secondary_without_session_or_queue_stays_disconnected(tmp_path):
    csms = await FakeCsms().start()
    try:
        config = make_config({"name": "tap", "url": csms.url})
        channel = _unattached_channel(config, StateStore(tmp_path / "CH1.json"))
        channel.start_background()
        try:
            await asyncio.sleep(0.3)
            assert not csms.connected.is_set()
        finally:
            await channel.close()
    finally:
        await csms.stop()


async def test_restored_queue_is_delivered_without_a_session_and_then_disconnects(tmp_path):
    csms = await FakeCsms().start()
    try:
        config = make_config({"name": "tap", "url": csms.url})
        store = StateStore(tmp_path / "CH1.json")
        store.state.outboxes["tap"] = [
            {"call": {"id": "m", "action": "MeterValues", "payload": {"connectorId": 1}}, "start_ref": None}
        ]
        store.save()
        channel = _unattached_channel(config, StateStore(tmp_path / "CH1.json"))
        channel.start_background()
        try:
            await csms.wait_for_call("MeterValues")
            for _ in range(100):  # drained and nobody attached: the link is closed again
                if not csms.connected.is_set():
                    break
                await asyncio.sleep(0.05)
            assert not csms.connected.is_set()
        finally:
            await channel.close()
    finally:
        await csms.stop()


class _ScriptedLink:
    """A link that answers each Call with the next scripted reply (a callable of the call)."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.sent = []

    async def call(self, call, timeout):
        self.sent.append(call)
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return reply(call)


def _rejected(call):
    return CallError(call.id, "InternalError", "try later")


def _accepted(call):
    return CallResult(call.id, {})


def _recording_sleep(channel):
    delays = []

    async def sleep(delay):
        delays.append(delay)

    channel._sleep = sleep
    return delays


async def test_a_rejected_billing_message_is_retried_and_then_delivered_once(tmp_path):
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", StateStore(tmp_path / "CH1.json"))
    delays = _recording_sleep(channel)
    meter = _QueuedCall(Call("m", "MeterValues", {"connectorId": 1}), durable=True)
    channel._queue.append(meter)
    link = _ScriptedLink(_rejected, _accepted)

    await channel._send_head(link)
    assert channel._queue[0] is meter  # kept for another attempt, after a backoff delay
    assert len(delays) == 1
    await channel._send_head(link)

    assert not channel._queue
    assert [call.action for call in link.sent] == ["MeterValues", "MeterValues"]


async def test_a_billing_message_rejected_every_time_is_dropped_after_the_attempt_limit(tmp_path, caplog):
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", StateStore(tmp_path / "CH1.json"))
    delays = _recording_sleep(channel)
    channel._transactions.primary_started("a", 100)
    channel._transactions.secondary_started("tap", "a", 9)
    stop = _QueuedCall(Call("s", "StopTransaction", {"transactionId": 100, "meterStop": 1}), durable=True)
    after = _QueuedCall(Call("m", "MeterValues", {"connectorId": 1}), durable=True)
    channel._queue.extend([stop, after])
    link = _ScriptedLink(*[_rejected] * MAX_CALL_ERROR_ATTEMPTS, _accepted)

    for _ in range(MAX_CALL_ERROR_ATTEMPTS):
        assert channel._queue[0] is stop  # later messages wait behind it
        await channel._send_head(link)

    assert list(channel._queue) == [after]
    assert channel._transactions.to_secondary(100, "tap") is None  # its transaction link is freed
    assert "tap rejected StopTransaction 5 times" in caplog.text
    assert len(delays) == MAX_CALL_ERROR_ATTEMPTS - 1
    await channel._send_head(link)
    assert not channel._queue


async def test_retry_delay_grows_while_a_message_keeps_timing_out(tmp_path):
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", StateStore(tmp_path / "CH1.json"))
    channel._queue.append(_QueuedCall(Call("m", "MeterValues", {"connectorId": 1}), durable=True))
    channel._retry_backoff = Backoff(1, 300)  # the real schedule, not the fast test one
    reconnect = Backoff()

    for _ in range(4):
        reconnect.reset()  # what an accepted boot does to the reconnect schedule
        with pytest.raises(BackendUnavailable):
            await channel._send_head(_SilentLink())
        channel._next_delay(reconnect)

    assert channel._retry_backoff.current == 16  # 1, 2, 4, 8 used: not back to 1 after each boot

    await channel._send_head(_ScriptedLink(_accepted))
    assert channel._retry_backoff.current == 1  # a delivered message resets it


async def test_a_queued_message_no_longer_forwarded_is_discarded(tmp_path, caplog):
    config = make_config({"name": "tap", "url": "ws://s", "forward_actions": ["StopTransaction"]})
    channel = make_channel(config, "tap", StateStore(tmp_path / "CH1.json"))
    channel._queue.append(_QueuedCall(Call("m", "MeterValues", {"connectorId": 1}), durable=True))
    link = _ScriptedLink(_accepted)

    await channel._send_head(link)

    assert not channel._queue
    assert not link.sent
    assert "tap no longer forwards MeterValues" in caplog.text


async def test_a_rejected_status_replay_is_logged(tmp_path, caplog):
    store = StateStore(tmp_path / "CH1.json")
    store.state.statuses["1"] = {"connectorId": 1, "status": "Available", "errorCode": "NoError"}
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", store)

    await channel._send_statuses(_ScriptedLink(_rejected))

    assert "tap rejected StatusNotification: InternalError" in caplog.text


async def test_card_rejection_warning_names_the_backend(tmp_path, caplog):
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", StateStore(tmp_path / "CH1.json"))
    item = _QueuedCall(Call("a", "Authorize", {"idTag": "X"}), durable=False)

    channel._handle_result(item, CallResult("a", {"idTagInfo": {"status": "Invalid"}}))

    assert "secondary backend tap answered Authorize with idTag status 'Invalid'" in caplog.text


async def test_the_chargers_own_boot_is_retried_until_accepted(tmp_path):
    channel = make_channel(make_config({"name": "tap", "url": "ws://s"}), "tap", StateStore(tmp_path / "CH1.json"))
    delays = _recording_sleep(channel)
    channel._queue.append(_QueuedCall(Call("b", "BootNotification", {"chargePointVendor": "v"}), durable=False))

    def pending(call):
        return CallResult(call.id, {"status": "Pending", "currentTime": "t", "interval": 30})

    def accepted(call):
        return CallResult(call.id, {"status": "Accepted", "currentTime": "t", "interval": 7})

    link = _ScriptedLink(pending, accepted)

    await channel._send_head(link)

    assert [call.action for call in link.sent] == ["BootNotification", "BootNotification"]
    assert delays == [30]  # the interval the backend asked for
    assert not channel._queue
