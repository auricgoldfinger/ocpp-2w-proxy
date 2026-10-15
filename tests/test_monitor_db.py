import json
import sqlite3
import zlib

import pytest

from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.rows import DecisionRow, ExchangeRow, FrameRow, LinkRow, WriteBatch


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "monitor.sqlite")
    db.prepare_writer(connection)
    yield connection
    connection.close()


def exchange(exchange_id=1, **changes):
    row = ExchangeRow(
        exchange_id=exchange_id,
        charger_id="cp",
        origin="charger",
        action="StopTransaction",
        started_at=10.0,
        last_at=10.1,
        outcome="queued",
        problem=False,
        incomplete=False,
        transaction_id=10216,
        connector_id=1,
        id_tag=None,
        not_sent=("stats",),
        headline="1.0 kWh",
    )
    return row._replace(**changes)


def frame(exchange_id=1, payload='{"a":1}', at=10.0):
    return FrameRow(exchange_id, at, "charger", "in", "call", "w1", payload)


def scalar(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def test_pragmas_and_schema_version(conn):
    assert scalar(conn, "PRAGMA journal_mode") == "wal"
    assert scalar(conn, "PRAGMA synchronous") == 1  # NORMAL
    assert scalar(conn, "PRAGMA foreign_keys") == 1
    assert scalar(conn, "PRAGMA auto_vacuum") == 0
    assert scalar(conn, "PRAGMA user_version") == len(db.MIGRATIONS)


def test_preparing_again_runs_no_migration_twice(conn):
    db.prepare_writer(conn)

    assert scalar(conn, "PRAGMA user_version") == len(db.MIGRATIONS)


def test_only_new_migrations_run(conn, monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, "CREATE TABLE extra (x);"))

    db.migrate(conn)
    db.migrate(conn)

    assert scalar(conn, "PRAGMA user_version") == len(db.MIGRATIONS)
    assert scalar(conn, "SELECT count(*) FROM sqlite_master WHERE name = 'extra'") == 1


def test_upsert_keeps_origin_started_at_and_not_sent(conn):
    db.write_batch(conn, WriteBatch(exchanges=[exchange()]))
    resumed = exchange(
        origin="proxy",
        started_at=400.0,
        last_at=400.2,
        outcome="delivered",
        not_sent=(),
        headline=None,
        connector_id=None,
    )
    db.write_batch(conn, WriteBatch(exchanges=[resumed]))

    row = conn.execute(
        "SELECT origin, started_at, last_at, outcome, not_sent, headline, connector_id FROM exchanges"
    ).fetchone()
    assert row == ("charger", 10.0, 400.2, "delivered", '["stats"]', "1.0 kWh", 1)
    assert scalar(conn, "SELECT count(*) FROM exchanges") == 1


def test_upsert_never_moves_last_at_back_nor_clears_incomplete(conn):
    db.write_batch(conn, WriteBatch(exchanges=[exchange(last_at=50.0, incomplete=True)]))
    db.write_batch(conn, WriteBatch(exchanges=[exchange(last_at=20.0, incomplete=False)]))

    assert conn.execute("SELECT last_at, incomplete FROM exchanges").fetchone() == (50.0, 1)


def test_frame_payload_is_compressed_or_null(conn):
    db.write_batch(conn, WriteBatch(exchanges=[exchange()], frames=[frame(), frame(payload=None, at=10.1)]))

    payloads = [row[0] for row in conn.execute("SELECT payload FROM frames ORDER BY at")]
    assert json.loads(zlib.decompress(payloads[0])) == {"a": 1}
    assert payloads[1] is None


def test_batch_writes_decisions_links_gaps_and_incomplete_marks(conn):
    db.write_batch(conn, WriteBatch(exchanges=[exchange(1), exchange(2)]))
    db.write_batch(
        conn,
        WriteBatch(
            decisions=[DecisionRow(1, 10.1, "primary", "QUEUED", None)],
            links=[LinkRow("cp", "primary", 9.0, False)],
            gaps=[Gap("cp", 9.5, 9.9, 37)],
            incomplete=[2],
        ),
    )

    assert conn.execute("SELECT exchange_rowid, peer, reason FROM decisions").fetchall() == [(1, "primary", "QUEUED")]
    assert conn.execute("SELECT * FROM links").fetchall() == [("cp", "primary", 9.0, 0)]
    assert conn.execute("SELECT * FROM monitor_gaps").fetchall() == [("cp", 9.5, 9.9, 37)]
    assert conn.execute("SELECT exchange_id FROM exchanges WHERE incomplete").fetchall() == [(2,)]


def test_a_failing_batch_writes_nothing(conn):
    bad = exchange(2, charger_id=None)
    with pytest.raises(sqlite3.IntegrityError):
        db.write_batch(conn, WriteBatch(exchanges=[exchange(1), bad]))

    assert scalar(conn, "SELECT count(*) FROM exchanges") == 0


def test_purge_chunk_deletes_old_rows_with_their_frames_in_chunks(conn):
    old = [exchange(n, last_at=float(n)) for n in range(1, 6)]
    db.write_batch(
        conn,
        WriteBatch(
            exchanges=[*old, exchange(99, last_at=1000.0)],
            frames=[frame(n) for n in (1, 2, 3, 4, 5, 99)],
            decisions=[DecisionRow(1, 1.0, "primary", "QUEUED", None)],
            links=[LinkRow("cp", "primary", 1.0, True), LinkRow("cp", "primary", 2000.0, False)],
            gaps=[Gap("cp", 1.0, 2.0, 1)],
        ),
    )

    assert db.purge_chunk(conn, cutoff=100.0, limit=3) == 5  # 3 exchanges, 1 link, 1 gap
    assert db.purge_chunk(conn, cutoff=100.0, limit=3) == 2
    assert db.purge_chunk(conn, cutoff=100.0, limit=3) == 0

    assert conn.execute("SELECT exchange_id FROM exchanges").fetchall() == [(99,)]
    assert scalar(conn, "SELECT count(*) FROM frames") == 1
    assert scalar(conn, "SELECT count(*) FROM decisions") == 0
    assert scalar(conn, "SELECT count(*) FROM links") == 1
