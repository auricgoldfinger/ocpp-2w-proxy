import asyncio
import sqlite3

import pytest

from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.retention import RetentionPurge
from ocpp_2w_proxy.monitor.rows import ExchangeRow, LinkRow, WriteBatch
from ocpp_2w_proxy.monitor.sqlite_worker import SqliteWorker

DAY = 86_400.0
NOW = 100 * DAY


def row(exchange_id, last_at):
    return ExchangeRow(
        exchange_id=exchange_id,
        charger_id="cp",
        origin="charger",
        action="Heartbeat",
        started_at=last_at,
        last_at=last_at,
        outcome="delivered",
        problem=False,
        incomplete=False,
        transaction_id=None,
        connector_id=None,
        id_tag=None,
        not_sent=(),
        headline=None,
    )


@pytest.fixture
async def store(tmp_path):
    worker = SqliteWorker(tmp_path / "monitor.sqlite", readonly=False)
    await worker.run(db.prepare_writer)
    old, recent = NOW - 91 * DAY, NOW - 89 * DAY
    batch = WriteBatch(
        exchanges=[row(i, old) for i in range(5)] + [row(100, recent)],
        links=[LinkRow("cp", "primary", old, True), LinkRow("cp", "primary", recent, False)],
        gaps=[Gap("cp", old, old, 3)],
    )
    await worker.run(db.write_batch, batch)
    yield worker
    await worker.close()


async def count(store, table):
    return (await store.run(lambda conn: conn.execute(f"SELECT count(*) FROM {table}").fetchone()))[0]


class CountingJobs:
    def __init__(self, worker):
        self._worker = worker
        self.jobs = 0

    async def run(self, fn, *args):
        self.jobs += 1
        return await self._worker.run(fn, *args)


async def test_purge_deletes_rows_past_retention_one_chunk_per_job(store):
    jobs = CountingJobs(store)
    purge = RetentionPurge(jobs, retention_days=90, chunk=2, clock=lambda: NOW)

    deleted = await purge.purge()

    assert deleted == 5 + 1 + 1
    assert jobs.jobs == 4  # 2 + 2 + 1 exchanges (the link and gap ride along), then an empty chunk
    assert (await count(store, "exchanges"), await count(store, "links"), await count(store, "monitor_gaps")) == (
        1,
        1,
        0,
    )


async def test_purge_yields_to_the_loop_between_chunks(store, monkeypatch):
    sleeps = []
    real_sleep = asyncio.sleep

    async def recording_sleep(delay, *args):
        sleeps.append(delay)
        return await real_sleep(delay, *args)

    monkeypatch.setattr("ocpp_2w_proxy.monitor.retention.asyncio.sleep", recording_sleep)
    purge = RetentionPurge(store, retention_days=90, chunk=2, clock=lambda: NOW)

    await purge.purge()

    assert sleeps == [0, 0, 0]


async def test_the_task_purges_at_start_and_again_every_interval(store):
    purge = RetentionPurge(store, retention_days=90, interval=0.05, clock=lambda: NOW)

    purge.start()
    await asyncio.sleep(0.02)
    assert await count(store, "exchanges") == 1
    await store.run(db.write_batch, WriteBatch(exchanges=[row(7, 0.0)]))
    await asyncio.sleep(0.08)
    await purge.close()

    assert await count(store, "exchanges") == 1


class Failing:
    def __init__(self):
        self.calls = 0

    async def run(self, fn, *args):
        self.calls += 1
        raise sqlite3.OperationalError("database is locked")


async def test_a_failing_purge_is_logged_and_retried_next_interval(caplog):
    failing = Failing()
    purge = RetentionPurge(failing, retention_days=90, interval=0.02)

    purge.start()
    await asyncio.sleep(0.07)
    await purge.close()

    assert failing.calls >= 2
    assert caplog.text.count("monitor retention purge failed") == 1
