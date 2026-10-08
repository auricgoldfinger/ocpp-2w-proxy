from ocpp_2w_proxy.state import StateStore


def test_state_store_round_trip_and_corruption(tmp_path):
    store = StateStore(tmp_path / "c.json")
    store.state.transactions["1"] = {"tap": 2}
    store.state.outboxes["tap"] = [{"call": {"id": "m", "action": "MeterValues", "payload": {}}}]
    store.state.primary_outbox.append(
        {"id": "message-1", "action": "StatusNotification", "payload": {"connectorId": 1}}
    )
    store.save()
    restored = StateStore(tmp_path / "c.json").state
    assert restored.transactions == {"1": {"tap": 2}}
    assert restored.outboxes == {"tap": [{"call": {"id": "m", "action": "MeterValues", "payload": {}}}]}
    assert restored.primary_outbox == [
        {"id": "message-1", "action": "StatusNotification", "payload": {"connectorId": 1}}
    ]

    (tmp_path / "c.json").write_text("{garbage")
    assert StateStore(tmp_path / "c.json").state.primary_outbox == []
    assert (tmp_path / "c.corrupt").exists()


def test_v1_state_file_is_parked_for_inspection(tmp_path):
    path = tmp_path / "c.json"
    path.write_text('{"version": 1, "transactions": {"1": 2}, "outbox": []}')
    store = StateStore(path)
    assert store.state.transactions == {}
    assert (tmp_path / "c.corrupt").exists()  # undelivered billing data is kept, never silently discarded
