import asyncio
import json
import sqlite3
import time
from collections import Counter

from ocpp_2w_proxy.monitor import db, payload_blob
from ocpp_2w_proxy.monitor.batch_assembler import BatchAssembler
from ocpp_2w_proxy.monitor.live_hub import LiveHub
from ocpp_2w_proxy.monitor.not_sent import NotSentMap
from ocpp_2w_proxy.monitor.queries import ExchangeQuery, exchange_detail, list_exchanges
from ocpp_2w_proxy.monitor.recorder_hub import RecorderHub
from ocpp_2w_proxy.monitor.writer import MonitorWriter
from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.reasons import Reason

CP = "cp"
NOT_SENT = NotSentMap({CP: {}}, {CP: ()})
SECRET_CONFIG = {"configurationKey": [{"key": "AuthorizationKey", "value": "s3cret-key", "readonly": False}]}


def new_writer(store, live=None, recorders=None, **options):
    """A writer and the recorder of charger ``CP`` that feeds it."""
    recorders = recorders or RecorderHub([CP])
    options.setdefault("batch_window", 0.01)
    return MonitorWriter(recorders, store, live or LiveHub(), NOT_SENT, **options), recorders.recorder_for(CP)


async def run_writer(writer):
    """Start the writer, let it take what is queued, and close it."""
    writer.start()
    await asyncio.sleep(0.05)
    await writer.close()


async def exchanges(store):
    return json.loads(await store.run(list_exchanges, ExchangeQuery()))["exchanges"]


def published(subscription):
    frames = []
    while not subscription._queue.empty():
        frames.append(subscription._queue.get_nowait())
    return frames


async def stored_payloads(store):
    blobs = await store.run(lambda conn: conn.execute("SELECT payload FROM frames ORDER BY rowid").fetchall())
    return [None if blob is None else payload_blob.decode(blob) for (blob,) in blobs]


def forwarded(recorder, n):
    """``n`` charger Heartbeats relayed to the primary and answered: four events each."""
    for i in range(n):
        call = Call(f"w{i}", "Heartbeat", {})
        recorder.received("charger", call)
        recorder.sent("primary", call)
        recorder.received("primary", CallResult(f"w{i}", {"currentTime": "t"}))
        recorder.sent("charger", CallResult(f"w{i}", {"currentTime": "t"}))


async def test_events_end_up_stored_as_projected_exchanges(monitor_store):
    writer, recorder = new_writer(monitor_store)
    forwarded(recorder, 3)

    await run_writer(writer)

    listed = await exchanges(monitor_store)
    assert [(e["action"], e["outcome"], len(e["legs"])) for e in listed] == [("Heartbeat", "delivered", 1)] * 3
    assert writer.dropped_batches == 0


async def test_each_changed_summary_and_each_link_change_is_published_once(monitor_store):
    live = LiveHub()
    subscription = live.subscribe()
    writer, recorder = new_writer(monitor_store, live)
    recorder.link_changed("primary", True, "boot replayed")
    forwarded(recorder, 2)

    await run_writer(writer)

    frames = published(subscription)
    events = [frame.split(b"\n")[0] for frame in frames]
    assert events == [b"event: link", b"event: exchange", b"event: exchange"]
    link = json.loads(frames[0].split(b"data: ")[1])
    assert (link["charger_id"], link["peer"], link["up"], link["note"]) == (CP, "primary", True, "boot replayed")


async def test_never_more_than_fifty_events_between_yields(monitor_store, monkeypatch):
    """Not while draining the queue either: taking 500 events off it is a slice's worth of work."""
    recorders = RecorderHub([CP])
    writer, recorder = new_writer(monitor_store, recorders=recorders, batch_window=0.5)
    forwarded(recorder, 250)  # 1000 events, two full batches
    ticks = 0
    seen = Counter()

    def spy(name):
        original = getattr(BatchAssembler, name)

        def counted(self, *args):
            seen[name, ticks] += 1
            return original(self, *args)

        monkeypatch.setattr(BatchAssembler, name, counted)

    for name in ("correlate", "fold", "summarize", "record"):
        spy(name)
    get_nowait = recorders.queue.get_nowait

    def drained():
        seen["drain", ticks] += 1
        return get_nowait()

    monkeypatch.setattr(recorders.queue, "get_nowait", drained)

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    ticking = asyncio.create_task(ticker())
    try:
        await run_writer(writer)
    finally:
        ticking.cancel()

    assert sum(count for (name, _), count in seen.items() if name == "fold") == 1000
    assert sum(count for (name, _), count in seen.items() if name == "drain") >= 1000
    assert max(seen.values()) <= 50


async def test_without_live_subscribers_no_summary_is_built(monitor_store, monkeypatch):
    writer, recorder = new_writer(monitor_store)
    forwarded(recorder, 2)
    summarized = []
    original = BatchAssembler.summarize
    monkeypatch.setattr(BatchAssembler, "summarize", lambda self, tracked: summarized.append(original(self, tracked)))

    await run_writer(writer)

    assert summarized == []
    assert len(await exchanges(monitor_store)) == 2


async def test_reply_is_correlated_before_it_is_redacted(monitor_store):
    writer, recorder = new_writer(monitor_store)
    command = Call("g1", "GetConfiguration", {"key": ["AuthorizationKey"]})
    recorder.received("primary", command)
    recorder.sent("charger", command.with_id("c1"))
    recorder.received("charger", CallResult("c1", SECRET_CONFIG))
    recorder.sent("primary", CallResult("g1", SECRET_CONFIG))

    await run_writer(writer)

    (exchange,) = await exchanges(monitor_store)
    assert exchange["action"] == "GetConfiguration"
    assert exchange["legs"][0]["outcome"] == "delivered"
    replies = [p for p in await stored_payloads(monitor_store) if p and "configurationKey" in p]
    assert [r["configurationKey"][0]["value"] for r in replies] == ["***", "***"]
    assert SECRET_CONFIG["configurationKey"][0]["value"] == "s3cret-key"  # the relayed payload is untouched


async def test_queue_overflow_becomes_a_gap_row_and_marks_the_open_exchange_incomplete(monitor_store):
    live = LiveHub()
    subscription = live.subscribe()
    writer, recorder = new_writer(monitor_store, live, RecorderHub([CP], per_charger=2))
    call = Call("w1", "MeterValues", {"connectorId": 1})
    recorder.received("charger", call)
    recorder.sent("primary", call)
    recorder.received("primary", CallResult("w1", {}))  # dropped: the queue holds two
    recorder.sent("charger", CallResult("w1", {}))  # dropped

    await run_writer(writer)

    gaps = await monitor_store.run(lambda conn: conn.execute("SELECT charger_id, dropped FROM monitor_gaps").fetchall())
    assert gaps == [(CP, 2)]
    (exchange,) = await exchanges(monitor_store)
    assert exchange["incomplete"] is True
    assert exchange["legs"][0]["reason_text"] == "Unknown (monitor gap)"
    frames = published(subscription)
    assert [frame.split(b"\n")[0] for frame in frames] == [b"event: gap", b"event: exchange"]
    assert b'"incomplete":true' in frames[1]


class FailingOnce:
    """A write worker whose first batch write fails."""

    def __init__(self, worker):
        self._worker = worker
        self.failed = False

    async def run(self, fn, *args):
        if fn is db.write_batch and not self.failed:
            self.failed = True
            raise sqlite3.OperationalError("disk I/O error")
        return await self._worker.run(fn, *args)


async def test_the_writer_survives_a_failing_database(monitor_store, caplog):
    writer, recorder = new_writer(FailingOnce(monitor_store))
    writer.start()
    forwarded(recorder, 1)
    await asyncio.sleep(0.05)

    forwarded(recorder, 2)
    await asyncio.sleep(0.05)
    await writer.close()

    assert writer.dropped_batches == 1
    assert "monitor store write failed" in caplog.text
    assert len(await exchanges(monitor_store)) == 2


async def test_a_queued_call_delivered_after_a_writer_restart_is_one_exchange(monitor_store):
    stop = Call("s1", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921, "idTag": "04A1B2C3D4"})
    writer, recorder = new_writer(monitor_store)
    recorder.received("charger", stop)
    recorder.decided("primary", stop, Reason.QUEUED)
    recorder.sent("charger", CallResult("s1", {}))
    await run_writer(writer)

    writer, recorder = new_writer(monitor_store)  # a restarted proxy: the call came back from the state file
    restored = Call("s1", "StopTransaction", dict(stop.payload), exchange_id=stop.exchange_id)
    recorder.sent("primary", restored.with_id("q1"))
    recorder.received("primary", CallResult("q1", {"idTagInfo": {"status": "Accepted"}}))
    await run_writer(writer)

    (exchange,) = await exchanges(monitor_store)
    assert exchange["id"] == str(stop.exchange_id)
    assert (exchange["origin"], exchange["outcome"], exchange["id_tag"]) == ("charger", "delivered", "***C3D4")
    (primary,) = exchange["legs"]
    assert [primary[key] for key in ("peer", "reason", "wire_id", "rewritten")] == ["primary", "QUEUED", "q1", []]
    detail = json.loads(await monitor_store.run(exchange_detail, stop.exchange_id))
    assert [(f["peer"], f["dir"], f["same_as_origin"]) for f in detail["frames"]] == [
        ("charger", "in", False),
        ("charger", "out", False),
        ("primary", "out", True),
        ("primary", "in", False),
    ]


async def test_close_flushes_what_is_still_queued(monitor_store):
    recorders = RecorderHub([CP])
    writer, recorder = new_writer(monitor_store, recorders=recorders)
    forwarded(recorder, 2)

    await writer.close()

    assert len(await exchanges(monitor_store)) == 2
    assert recorders.queue.empty()


async def test_counts_the_frames_per_peer_for_the_last_hour(monitor_store):
    writer, recorder = new_writer(monitor_store)
    forwarded(recorder, 3)
    recorder.link_changed("primary", True)

    await run_writer(writer)

    now = time.time()
    assert writer.rates.last_hour(CP, "charger", now) == 6
    assert writer.rates.last_hour(CP, "primary", now) == 6
