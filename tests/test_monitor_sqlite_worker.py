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


async def test_runs_fn_with_the_connection_in_its_own_thread(tmp_path):
    worker = SqliteWorker(tmp_path / "m.sqlite", readonly=False)
    try:
        await worker.run(create, 42)
        rows, thread = await worker.run(read)
    finally:
        await worker.close()

    assert rows == [(42,)]
    assert thread.startswith("monitor-sqlite-writer")
    assert thread != threading.current_thread().name


async def test_every_job_uses_the_same_thread(tmp_path):
    worker = SqliteWorker(tmp_path / "m.sqlite", readonly=False)
    try:
        threads = {(await worker.run(lambda conn: threading.get_ident())) for _ in range(5)}
    finally:
        await worker.close()

    assert len(threads) == 1


async def test_readonly_worker_reads_but_cannot_write(tmp_path):
    path = tmp_path / "m.sqlite"
    writer = SqliteWorker(path, readonly=False)
    reader = SqliteWorker(path, readonly=True)
    try:
        await writer.run(create, 1)
        rows, thread = await reader.run(read)
        assert rows == [(1,)]
        assert thread.startswith("monitor-sqlite-reader")
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            await reader.run(lambda conn: conn.execute("INSERT INTO t VALUES (2)"))
    finally:
        await reader.close()
        await writer.close()


async def test_errors_reach_the_caller(tmp_path):
    worker = SqliteWorker(tmp_path / "m.sqlite", readonly=False)
    try:
        with pytest.raises(sqlite3.OperationalError):
            await worker.run(lambda conn: conn.execute("SELECT * FROM missing"))
    finally:
        await worker.close()


async def test_readonly_worker_cannot_open_a_missing_database(tmp_path):
    worker = SqliteWorker(tmp_path / "absent.sqlite", readonly=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            await worker.run(read)
    finally:
        await worker.close()
