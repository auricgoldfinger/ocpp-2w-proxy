from ocpp_2w_proxy.state import StateStore


def test_state_store_round_trip_and_corruption(tmp_path):
    store = StateStore(tmp_path / "c.json")
    store.state.transactions["1"] = 2
    store.state.primary_outbox.append(
        {"id": "message-1", "action": "StatusNotification", "payload": {"connectorId": 1}}
    )
    store.save()
    restored = StateStore(tmp_path / "c.json").state
    assert restored.transactions == {"1": 2}
    assert restored.primary_outbox == [
        {"id": "message-1", "action": "StatusNotification", "payload": {"connectorId": 1}}
    ]

    (tmp_path / "c.json").write_text("{garbage")
    assert StateStore(tmp_path / "c.json").state.primary_outbox == []
    assert (tmp_path / "c.corrupt").exists()
