import asyncio
import os

from ocpp_2w_proxy.state import FLUSH_DELAY, StateStore, restore_outbox


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


async def test_save_soon_coalesces_a_burst_into_one_write(tmp_path, monkeypatch):
    """Every message used to rewrite and fsync the whole state file, once per backend
    per message: a burst must collapse into one write."""
    store = StateStore(tmp_path / "c.json")
    writes = 0
    real_write = store._write

    def counting_write():
        nonlocal writes
        writes += 1
        real_write()

    monkeypatch.setattr(store, "_write", counting_write)
    for _ in range(5):
        store.save_soon()
        await asyncio.sleep(0)

    await asyncio.sleep(FLUSH_DELAY + 0.1)
    assert writes == 1
    assert store.path.exists()  # the coalesced write happened


async def test_flush_writes_pending_changes_at_once(tmp_path):
    store = StateStore(tmp_path / "c.json")
    store.state.transactions["1"] = {"tap": 2}
    store.save_soon()

    store.flush()  # a channel closing does not wait out the delay

    assert StateStore(tmp_path / "c.json").state.transactions == {"1": {"tap": 2}}
    assert store._flusher is None


def test_disk_write_errors_are_logged_not_raised(tmp_path, monkeypatch):
    """A full or read-only disk must not crash the relay tasks that persist queues."""

    def no_space(file_descriptor):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(os, "fsync", no_space)
    store = StateStore(tmp_path / "c.json")
    store.state.transactions["1"] = {"tap": 2}
    store.save()  # logged, not raised

    monkeypatch.undo()  # the disk has space again
    store.save()
    assert StateStore(tmp_path / "c.json").state.transactions == {"1": {"tap": 2}}
    path = tmp_path / "c.json"
    for text in ("{first", "{second", "{third"):
        path.write_text(text)
        StateStore(path)
    assert (tmp_path / "c.corrupt").read_text() == "{first"
    assert (tmp_path / "c.corrupt.1").read_text() == "{second"
    assert (tmp_path / "c.corrupt.2").read_text() == "{third"
