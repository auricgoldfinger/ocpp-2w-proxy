from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.secondary_channel import SecondaryChannel
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog
from ocpp_2w_proxy.transactions import TransactionMap


def make_channel(tmp_path, max_queue=3):
    config = parse(
        {
            "chargers": [{"id": "CH1"}],
            "primary": {"url": "ws://p"},
            "secondary": {"url": "ws://s", "max_queue": max_queue},
        },
        {},
    )
    store = StateStore(tmp_path / "CH1.json")

    async def on_call(call):  # pragma: no cover
        pass

    channel = SecondaryChannel(
        config.secondary, "ws://s/CH1", {}, None, store, TransactionMap(store), TrafficLog("CH1", False), on_call
    )
    return channel, store


def test_offline_keeps_only_durable_calls_and_caches_boot_and_status(tmp_path):
    channel, store = make_channel(tmp_path)
    channel.submit(Call("1", "BootNotification", {"chargePointVendor": "v", "chargePointModel": "m"}))
    channel.submit(Call("2", "StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"}))
    channel.submit(Call("3", "Heartbeat", {}))
    channel.submit(Call("4", "DataTransfer", {"vendorId": "x"}))
    channel.submit(Call("5", "StartTransaction", {"connectorId": 1}), start_ref="ref")

    state = StateStore(tmp_path / "CH1.json").state
    assert [item["call"]["action"] for item in state.outbox] == ["StartTransaction"]
    assert state.outbox[0]["start_ref"] == "ref"
    assert state.outbox[0]["call"]["id"] != "5"  # own id, charger ids restart after reboot
    assert state.boot["chargePointVendor"] == "v"
    assert state.statuses["1"]["status"] == "Available"


def test_queue_limit_drops_meter_values_first(tmp_path):
    channel, store = make_channel(tmp_path, max_queue=3)
    channel.submit(Call("1", "StartTransaction", {"connectorId": 1}))
    channel.submit(Call("2", "MeterValues", {"connectorId": 1, "meterValue": [1]}))
    channel.submit(Call("3", "MeterValues", {"connectorId": 1, "meterValue": [2]}))
    channel.submit(Call("4", "StopTransaction", {"transactionId": 1}))

    actions = [(item["call"]["action"], item["call"]["payload"].get("meterValue")) for item in store.state.outbox]
    assert actions == [("StartTransaction", None), ("MeterValues", [2]), ("StopTransaction", None)]


def test_queue_restored_from_disk(tmp_path):
    channel, _ = make_channel(tmp_path)
    channel.submit(Call("1", "StartTransaction", {"connectorId": 1}), start_ref="r")
    restored, _ = make_channel(tmp_path)
    assert [item.call.action for item in restored._queue] == ["StartTransaction"]
    assert restored._queue[0].start_ref == "r"
