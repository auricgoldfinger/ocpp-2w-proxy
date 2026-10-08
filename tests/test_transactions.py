from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.transactions import TransactionMap


def make(tmp_path):
    return TransactionMap(StateStore(tmp_path / "s.json"))


def test_links_regardless_of_which_backend_answers_first(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)
    txmap.secondary_started("stats", "b", 10)
    txmap.primary_started("b", 200)
    assert txmap.to_secondary(100, "tap") == 9
    assert txmap.to_secondary(200, "stats") == 10
    assert txmap.to_primary(10, "stats") == 200


def test_one_start_links_to_every_backend(tmp_path):
    txmap = make(tmp_path)
    txmap.secondary_started("tap", "a", 9)
    txmap.secondary_started("stats", "a", 10)
    txmap.primary_started("a", 100)
    assert txmap.to_secondary(100, "tap") == 9
    assert txmap.to_secondary(100, "stats") == 10
    assert txmap.to_primary(9, "tap") == 100
    assert txmap.to_primary(10, "stats") == 100


def test_mapping_survives_restart(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)
    assert make(tmp_path).to_secondary(100, "tap") == 9


def test_rewrite_for_secondary(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)

    meter = Call("1", "MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    assert txmap.rewrite_for_secondary(meter, "tap").payload["transactionId"] == 9

    unknown_meter = Call("1", "MeterValues", {"connectorId": 1, "transactionId": 555, "meterValue": []})
    assert "transactionId" not in txmap.rewrite_for_secondary(unknown_meter, "tap").payload

    stop = Call("1", "StopTransaction", {"transactionId": 100, "meterStop": 5, "timestamp": "t"})
    assert txmap.rewrite_for_secondary(stop, "tap").payload["transactionId"] == 9
    assert txmap.rewrite_for_secondary(Call("1", "StopTransaction", {"transactionId": 555}), "tap") is None
    # A transaction the other backend never saw is not sent there either.
    assert txmap.rewrite_for_secondary(Call("1", "StopTransaction", {"transactionId": 100}), "stats") is None

    heartbeat = Call("1", "Heartbeat", {})
    assert txmap.rewrite_for_secondary(heartbeat, "tap") is heartbeat


def test_rewrite_remote_stop_for_charger(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)
    assert txmap.rewrite_for_charger(Call("1", "RemoteStopTransaction", {"transactionId": 9}), "tap").payload == {
        "transactionId": 100
    }
    assert txmap.rewrite_for_charger(Call("1", "RemoteStopTransaction", {"transactionId": 1}), "tap") is None
    assert txmap.rewrite_for_charger(Call("1", "RemoteStopTransaction", {"transactionId": 9}), "stats") is None


def test_forget(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)
    txmap.secondary_started("stats", "a", 10)
    txmap.forget(100)
    assert txmap.to_secondary(100, "tap") is None
    assert txmap.to_secondary(100, "stats") is None


def test_forget_prevents_late_secondary_conf_from_resurrecting_the_mapping(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.forget(100)
    txmap.secondary_started("tap", "a", 9)  # the slow backend answers after the transaction ended
    assert txmap.to_secondary(100, "tap") is None
    assert make(tmp_path)._state.pending_primary_starts == {}


def test_late_secondary_links_through_the_pending_primary_record(tmp_path):
    txmap = make(tmp_path)
    txmap.primary_started("a", 100)
    txmap.secondary_started("tap", "a", 9)
    txmap.secondary_started("stats", "a", 10)  # answers much later, after tap already linked
    assert txmap.to_secondary(100, "tap") == 9
    assert txmap.to_secondary(100, "stats") == 10
