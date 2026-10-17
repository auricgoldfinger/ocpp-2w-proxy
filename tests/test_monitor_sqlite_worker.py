import sqlite3
import threading

import pytest

from ocpp_2w_proxy.monitor.sqlite_worker import SqliteWorker


def create(conn, value):
    conn.execute("CREATE TABLE t (v)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()


def read(conn):
    return conn.execute("SELECT v FROM t").fetchall(), threading.current_thread().name


@pytest.fixture
async def worker(tmp_path):
    worker = SqliteWorker(tmp_path / "m.sqlite", readonly=False)
    yield worker
    await worker.close()


@pytest.fixture
async def reader(tmp_path):
    reader = SqliteWorker(tmp_path / "m.sqlite", readonly=True)
    yield reader
    await reader.close()


async def test_runs_every_job_with_the_connection_in_the_same_own_thread(worker):
    await worker.run(create, 42)
    rows, thread = await worker.run(read)
    threads = {(await worker.run(lambda conn: threading.get_ident())) for _ in range(5)}

    assert rows == [(42,)]
    assert thread.startswith("monitor-sqlite-writer")
    assert thread != threading.current_thread().name
    assert len(threads) == 1


async def test_readonly_worker_reads_but_cannot_write(worker, reader):
    await worker.run(create, 1)
    rows, thread = await reader.run(read)

    assert rows == [(1,)]
    assert thread.startswith("monitor-sqlite-reader")
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        await reader.run(lambda conn: conn.execute("INSERT INTO t VALUES (2)"))


async def test_errors_reach_the_caller(worker):
    with pytest.raises(sqlite3.OperationalError):
        await worker.run(lambda conn: conn.execute("SELECT * FROM missing"))


async def test_readonly_worker_cannot_open_a_missing_database(reader):
    with pytest.raises(sqlite3.OperationalError):
        await reader.run(read)
