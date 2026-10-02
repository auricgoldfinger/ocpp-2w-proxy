from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.transactions import TransactionMap


def make(tmp_path):
    return TransactionMap(StateStore(tmp_path / "s.json"))


def test_links_regardless_of_which_backend_answers_first(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("a", 9)
    txmap.secondary_started("b", 10)
    txmap.primary_started("b", 200)
    assert txmap.to_secondary(100) == 9
    assert txmap.to_secondary(200) == 10
    assert txmap.to_primary(10) == 200


def test_mapping_survives_restart(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("a", 9)
    assert make(tmp_path).to_secondary(100) == 9


def test_rewrite_for_secondary(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("a", 9)

    meter = Call("1", "MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    assert txmap.rewrite_for_secondary(meter).payload["transactionId"] == 9

    unknown_meter = Call("1", "MeterValues", {"connectorId": 1, "transactionId": 555, "meterValue": []})
    assert "transactionId" not in txmap.rewrite_for_secondary(unknown_meter).payload

    stop = Call("1", "StopTransaction", {"transactionId": 100, "meterStop": 5, "timestamp": "t"})
    assert txmap.rewrite_for_secondary(stop).payload["transactionId"] == 9
    assert txmap.rewrite_for_secondary(Call("1", "StopTransaction", {"transactionId": 555})) is None

    heartbeat = Call("1", "Heartbeat", {})
    assert txmap.rewrite_for_secondary(heartbeat) is heartbeat


def test_rewrite_remote_stop_for_charger(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("a", 9)
    assert txmap.rewrite_for_charger(Call("1", "RemoteStopTransaction", {"transactionId": 9})).payload == {
        "transactionId": 100
    }
    assert txmap.rewrite_for_charger(Call("1", "RemoteStopTransaction", {"transactionId": 1})) is None


def test_forget(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("a", 9)
    txmap.forget(100)
    assert txmap.to_secondary(100) is None
