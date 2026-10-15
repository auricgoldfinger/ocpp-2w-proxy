import asyncio
import json
import logging
import sqlite3
import time
import zlib
from collections import Counter

import pytest

from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.batch_assembler import BatchAssembler
from ocpp_2w_proxy.monitor.live_hub import LiveHub
from ocpp_2w_proxy.monitor.not_sent import NotSentMap
from ocpp_2w_proxy.monitor.queries import ExchangeQuery, exchange_detail, list_exchanges
from ocpp_2w_proxy.monitor.recorder_hub import RecorderHub
from ocpp_2w_proxy.monitor.sqlite_worker import SqliteWorker
from ocpp_2w_proxy.monitor.writer import MonitorWriter
from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.reasons import Reason

CP = "cp"
NOT_SENT = NotSentMap({CP: {}}, {CP: ()})
SECRET_CONFIG = {"configurationKey": [{"key": "AuthorizationKey", "value": "s3cret-key", "readonly": False}]}


@pytest.fixture
async def store(tmp_path):
    worker = SqliteWorker(tmp_path / "monitor.sqlite", readonly=False)
    await worker.run(db.prepare_writer)
    yield worker
    await worker.close()


def new_writer(store, recorders=None, live=None, **options):
    recorders = recorders or RecorderHub([CP])
    options.setdefault("batch_window", 0.01)
    return MonitorWriter(recorders, store, live or LiveHub(), NOT_SENT, **options)


async def run_writer(writer):
    """Start the writer, let it take what is queued, and close it."""
    writer.start()
    await asyncio.sleep(0.05)
    await writer.close()


async def exchanges(store):
    return json.loads(await store.run(list_exchanges, ExchangeQuery()))["exchanges"]


async def stored_payloads(store):
    blobs = await store.run(lambda conn: conn.execute("SELECT payload FROM frames ORDER BY rowid").fetchall())
    return [None if blob is None else json.loads(zlib.decompress(blob)) for (blob,) in blobs]


def forwarded(recorder, n):
    """``n`` charger Heartbeats relayed to the primary and answered: four events each."""
    for i in range(n):
        call = Call(f"w{i}", "Heartbeat", {})
        recorder.received("charger", call)
        recorder.sent("primary", call)
        recorder.received("primary", CallResult(f"w{i}", {"currentTime": "t"}))
        recorder.sent("charger", CallResult(f"w{i}", {"currentTime": "t"}))


async def test_events_end_up_stored_as_projected_exchanges(store):
    recorders = RecorderHub([CP])
    writer = new_writer(store, recorders)
    forwarded(recorders.recorder_for(CP), 3)

    await run_writer(writer)

    listed = await exchanges(store)
    assert [(e["action"], e["outcome"], len(e["legs"])) for e in listed] == [("Heartbeat", "delivered", 1)] * 3
    assert writer.dropped_batches == 0


async def test_each_changed_summary_and_each_link_change_is_published_once(store):
    recorders, live = RecorderHub([CP]), LiveHub()
    subscription = live.subscribe()
    writer = new_writer(store, recorders, live)
    recorder = recorders.recorder_for(CP)
    recorder.link_changed("primary", True)
    forwarded(recorder, 2)

    await run_writer(writer)

    frames = []
    while not subscription._queue.empty():
        frames.append(subscription._queue.get_nowait())
    events = [frame.split(b"\n")[0] for frame in frames]
    assert events == [b"event: link", b"event: exchange", b"event: exchange"]
    link = json.loads(frames[0].split(b"data: ")[1])
    assert (link["charger_id"], link["peer"], link["up"]) == (CP, "primary", True)


async def test_never_more_than_fifty_events_between_yields(store, monkeypatch):
    recorders = RecorderHub([CP])
    forwarded(recorders.recorder_for(CP), 250)  # 1000 events, two full batches
    ticks = 0
    seen = Counter()

    def spy(name):
        original = getattr(BatchAssembler, name)

        def counted(self, *args):
            seen[name, ticks] += 1
            return original(self, *args)

        monkeypatch.setattr(BatchAssembler, name, counted)

    for name in ("correlate", "fold", "summarize"):
        spy(name)

    async def ticker():
        nonlocal ticks
        while True:
            ticks += 1
            await asyncio.sleep(0)

    ticking = asyncio.create_task(ticker())
    try:
        await run_writer(new_writer(store, recorders, batch_window=0.5))
    finally:
        ticking.cancel()

    assert sum(count for (name, _), count in seen.items() if name == "fold") == 1000
    assert max(seen.values()) <= 50


async def test_without_live_subscribers_no_summary_is_built(store, monkeypatch):
    recorders = RecorderHub([CP])
    forwarded(recorders.recorder_for(CP), 2)
    summarized = []
    original = BatchAssembler.summarize
    monkeypatch.setattr(BatchAssembler, "summarize", lambda self, tracked: summarized.append(original(self, tracked)))

    await run_writer(new_writer(store, recorders))

    assert summarized == []
    assert len(await exchanges(store)) == 2


async def test_reply_is_correlated_before_it_is_redacted(store):
    recorders = RecorderHub([CP])
    recorder = recorders.recorder_for(CP)
    command = Call("g1", "GetConfiguration", {"key": ["AuthorizationKey"]})
    recorder.received("primary", command)
    recorder.sent("charger", command.with_id("c1"))
    recorder.received("charger", CallResult("c1", SECRET_CONFIG))
    recorder.sent("primary", CallResult("g1", SECRET_CONFIG))

    await run_writer(new_writer(store, recorders))

    (exchange,) = await exchanges(store)
    assert exchange["action"] == "GetConfiguration"
    assert exchange["legs"][0]["outcome"] == "delivered"
    replies = [p for p in await stored_payloads(store) if p and "configurationKey" in p]
    assert [r["configurationKey"][0]["value"] for r in replies] == ["***", "***"]
    assert SECRET_CONFIG["configurationKey"][0]["value"] == "s3cret-key"  # the relayed payload is untouched


async def test_an_orphan_reply_is_stored_redacted(store):
    recorders = RecorderHub([CP])
    recorders.recorder_for(CP).received("primary", CallResult("zz", {"idTag": "04A1B2C3D4", "status": "Accepted"}))

    await run_writer(new_writer(store, recorders))

    (exchange,) = await exchanges(store)
    assert exchange["origin"] is None
    assert await stored_payloads(store) == [{"idTag": "***C3D4", "status": "Accepted"}]


async def test_queue_overflow_becomes_a_gap_row_and_marks_the_open_exchange_incomplete(store):
    recorders, live = RecorderHub([CP], per_charger=2), LiveHub()
    subscription = live.subscribe()
    writer = new_writer(store, recorders, live)
    recorder = recorders.recorder_for(CP)
    call = Call("w1", "MeterValues", {"connectorId": 1})
    recorder.received("charger", call)
    recorder.sent("primary", call)
    recorder.received("primary", CallResult("w1", {}))  # dropped: the queue holds two
    recorder.sent("charger", CallResult("w1", {}))  # dropped

    await run_writer(writer)

    gaps = await store.run(lambda conn: conn.execute("SELECT charger_id, dropped FROM monitor_gaps").fetchall())
    assert gaps == [(CP, 2)]
    (exchange,) = await exchanges(store)
    assert exchange["incomplete"] is True
    assert exchange["legs"][0]["reason_text"] == "Unknown (monitor gap)"
    frames = []
    while not subscription._queue.empty():
        frames.append(subscription._queue.get_nowait())
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


async def test_the_writer_survives_a_failing_database(store, caplog):
    recorders = RecorderHub([CP])
    recorder = recorders.recorder_for(CP)
    writer = new_writer(FailingOnce(store), recorders)
    writer.start()
    forwarded(recorder, 1)
    await asyncio.sleep(0.05)

    forwarded(recorder, 2)
    await asyncio.sleep(0.05)
    await writer.close()

    assert writer.dropped_batches == 1
    assert "monitor store write failed" in caplog.text
    assert len(await exchanges(store)) == 2


async def test_a_queued_call_delivered_after_a_writer_restart_is_one_exchange(store):
    stop = Call("s1", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921, "idTag": "04A1B2C3D4"})
    before = RecorderHub([CP])
    recorder = before.recorder_for(CP)
    recorder.received("charger", stop)
    recorder.decided("primary", stop, Reason.QUEUED)
    recorder.sent("charger", CallResult("s1", {}))
    await run_writer(new_writer(store, before))

    after = RecorderHub([CP])  # a restarted proxy: the call came back from the state file
    restored = Call("s1", "StopTransaction", dict(stop.payload), exchange_id=stop.exchange_id)
    recorder = after.recorder_for(CP)
    recorder.sent("primary", restored.with_id("q1"))
    recorder.received("primary", CallResult("q1", {"idTagInfo": {"status": "Accepted"}}))
    await run_writer(new_writer(store, after))

    (exchange,) = await exchanges(store)
    assert exchange["id"] == str(stop.exchange_id)
    assert (exchange["origin"], exchange["outcome"], exchange["id_tag"]) == ("charger", "delivered", "***C3D4")
    (primary,) = exchange["legs"]
    assert (primary["peer"], primary["reason"], primary["wire_id"], primary["rewritten"]) == (
        "primary",
        "QUEUED",
        "q1",
        [],
    )
    detail = json.loads(await store.run(exchange_detail, stop.exchange_id))
    assert [(f["peer"], f["dir"], f["same_as_origin"]) for f in detail["frames"]] == [
        ("charger", "in", False),
        ("charger", "out", False),
        ("primary", "out", True),
        ("primary", "in", False),
    ]


async def test_close_flushes_what_is_still_queued(store):
    recorders = RecorderHub([CP])
    forwarded(recorders.recorder_for(CP), 2)

    await new_writer(store, recorders).close()

    assert len(await exchanges(store)) == 2
    assert recorders.queue.empty()


async def test_a_thousand_event_burst_causes_no_slow_callback_in_writer_code(store, caplog):
    loop = asyncio.get_running_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = 0.005
    recorders = RecorderHub([CP])
    recorder = recorders.recorder_for(CP)
    caplog.set_level(logging.WARNING, logger="asyncio")
    try:
        writer = new_writer(store, recorders, batch_window=0.05)
        writer.start()
        for i in range(250):
            meter = {"connectorId": 1, "transactionId": i, "meterValue": [{"sampledValue": [{"value": str(i)}]}]}
            call = Call(f"w{i}", "MeterValues", meter)
            recorder.received("charger", call)
            recorder.sent("primary", call)
            recorder.sent("ha", Call(f"h{i}", "MeterValues", {**meter, "transactionId": i + 1}, call.exchange_id))
            recorder.received("primary", CallResult(f"w{i}", {}))
        await asyncio.sleep(0.3)
        await writer.close()
    finally:
        loop.set_debug(False)

    slow = [r.getMessage() for r in caplog.records if r.name == "asyncio" and "took" in r.getMessage()]
    assert [message for message in slow if "monitor" in message or "writer" in message.lower()] == []
    assert len(await exchanges(store)) == 200  # one page


async def test_counts_the_frames_per_peer_for_the_last_hour(store):
    recorders = RecorderHub([CP])
    writer = new_writer(store, recorders)
    recorder = recorders.recorder_for(CP)
    forwarded(recorder, 3)
    recorder.link_changed("primary", True)

    await run_writer(writer)

    now = time.time()
    assert writer.rates.last_hour(CP, "charger", now) == 6
    assert writer.rates.last_hour(CP, "primary", now) == 6
