import asyncio

from fakes import FakeCsms

from ocpp_2w_proxy.charger_auth import ChargerIdentity
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call, CallError
from ocpp_2w_proxy.secondary_channel import MAX_ATTEMPTS_PER_CALL, SecondaryChannel, _QueuedCall
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog
from ocpp_2w_proxy.transactions import TransactionMap


def make_config(*secondaries):
    return parse(
        {
            "chargers": [{"id": "CH1"}],
            "primary": {"url": "ws://p"},
            "secondary": [dict(entry, call_timeout=1) for entry in secondaries],
        },
        {},
    )


def make_channel(config, backend_name, store):
    async def on_call(call):  # pragma: no cover
        pass

    backend = next(b for b in config.secondaries if b.name == backend_name)
    charger = config.chargers["CH1"]
    channel = SecondaryChannel(backend, charger, store, TransactionMap(store), TrafficLog("CH1", False))
    channel.attach(ChargerIdentity(charger, None, None), on_call)
    return channel


def test_offline_keeps_only_durable_calls_and_caches_boot_and_status(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 3})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel.submit(Call("1", "BootNotification", {"chargePointVendor": "v", "chargePointModel": "m"}))
    channel.submit(Call("2", "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}))
    channel.submit(Call("3", "Heartbeat", {}))
    channel.submit(Call("4", "DataTransfer", {"vendorId": "x"}))
    channel.submit(Call("5", "StartTransaction", {"connectorId": 1}), start_ref="ref")

    state = StateStore(tmp_path / "CH1.json").state
    assert [item["call"]["action"] for item in state.outboxes["tap"]] == ["StartTransaction"]
    assert state.outboxes["tap"][0]["start_ref"] == "ref"
    assert state.outboxes["tap"][0]["call"]["id"] != "5"  # own id, charger ids restart after reboot
    assert state.boot["chargePointVendor"] == "v"
    assert state.statuses["1"]["status"] == "Available"


def test_each_backend_has_its_own_queue(tmp_path):
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


def test_queue_limit_drops_meter_values_first(tmp_path):
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


def test_queue_restored_from_disk(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s", "max_queue": 3})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel.submit(Call("1", "StartTransaction", {"connectorId": 1}), start_ref="r")
    restored = make_channel(config, "tap", StateStore(tmp_path / "CH1.json"))
    assert [item.call.action for item in restored._queue] == ["StartTransaction"]
    assert restored._queue[0].start_ref == "r"


def test_boot_and_status_replay_only_for_backends_that_forward_them(tmp_path):
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


async def test_dropped_stop_transaction_frees_the_transaction_link(tmp_path):
    config = make_config({"name": "tap", "url": "ws://s"})
    store = StateStore(tmp_path / "CH1.json")
    channel = make_channel(config, "tap", store)
    channel._transactions.primary_started("a", 100)
    channel._transactions.secondary_started("tap", "a", 9)
    stop = _QueuedCall(Call("m", "StopTransaction", {"transactionId": 100, "meterStop": 1}), durable=True)
    channel._queue.append(stop)
    stop.attempts = MAX_ATTEMPTS_PER_CALL - 1  # the next timeout drops it

    await channel._send_head(_SilentLink())

    assert channel._transactions.to_secondary(100, "tap") is None
    assert stop not in channel._queue
