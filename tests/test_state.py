from ocpp_2w_proxy.state import StateStore, restore_outbox


def test_restore_outbox_skips_unreadable_entries(tmp_path):
    store = StateStore(tmp_path / "c.json")
    store.state.primary_outbox = [
        {"id": "ok", "action": "MeterValues", "payload": {"connectorId": 1}},
        {"action": "missing id"},  # unreadable
        "not even a dict",  # unreadable
    ]
    store.state.outboxes["tap"] = [
        {"call": {"id": "st", "action": "StartTransaction", "payload": {}}, "start_ref": "r"},
        {"call": {"id": "bad"}, "start_ref": None},  # unreadable call
    ]
    store.save()
    restored = StateStore(tmp_path / "c.json").state

    assert [(call.id, ref) for call, ref in restore_outbox(restored.primary_outbox)] == [("ok", None)]
    assert [(call.id, ref) for call, ref in restore_outbox(restored.outboxes["tap"])] == [("st", "r")]


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


def test_corrupt_backups_are_numbered_not_overwritten(tmp_path):
    path = tmp_path / "c.json"
    for text in ("{first", "{second", "{third"):
        path.write_text(text)
        StateStore(path)
    assert (tmp_path / "c.corrupt").read_text() == "{first"
    assert (tmp_path / "c.corrupt.1").read_text() == "{second"
    assert (tmp_path / "c.corrupt.2").read_text() == "{third"
