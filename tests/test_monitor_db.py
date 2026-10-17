import json
import sqlite3

import pytest
from monitor_events import exchange_row

from ocpp_2w_proxy.monitor import db, payload_blob
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.rows import DecisionRow, FrameRow, LinkRow, WriteBatch


def frame(exchange_id=1, payload='{"a":1}', at=10.0):
    return FrameRow(exchange_id, at, "charger", "in", "call", "w1", payload)


def scalar(conn, sql, *params):
    return conn.execute(sql, params).fetchone()[0]


def rows(conn, sql):
    return conn.execute(sql).fetchall()


def test_pragmas_and_schema_version(monitor_conn):
    assert scalar(monitor_conn, "PRAGMA journal_mode") == "wal"
    assert scalar(monitor_conn, "PRAGMA synchronous") == 1  # NORMAL
    assert scalar(monitor_conn, "PRAGMA foreign_keys") == 1
    assert scalar(monitor_conn, "PRAGMA auto_vacuum") == 0
    assert scalar(monitor_conn, "PRAGMA user_version") == len(db.MIGRATIONS)


def test_preparing_again_runs_no_migration_twice(monitor_conn):
    db.prepare_writer(monitor_conn)

    assert scalar(monitor_conn, "PRAGMA user_version") == len(db.MIGRATIONS)


def test_only_new_migrations_run(monitor_conn, monkeypatch):
    monkeypatch.setattr(db, "MIGRATIONS", (*db.MIGRATIONS, "CREATE TABLE extra (x);"))

    db.migrate(monitor_conn)
    db.migrate(monitor_conn)

    assert scalar(monitor_conn, "PRAGMA user_version") == len(db.MIGRATIONS)
    assert scalar(monitor_conn, "SELECT count(*) FROM sqlite_master WHERE name = 'extra'") == 1


def test_a_version_1_database_keeps_its_links_and_gains_their_note(tmp_path):
    connection = sqlite3.connect(tmp_path / "v1.sqlite")
    connection.executescript(f"{db.MIGRATIONS[0]}\nPRAGMA user_version = 1;")
    connection.execute("INSERT INTO links (charger_id, peer, at, up) VALUES ('cp', 'primary', 9.0, 0)")
    connection.commit()

    db.prepare_writer(connection)

    assert connection.execute("SELECT * FROM links").fetchall() == [("cp", "primary", 9.0, 0, None)]
    connection.close()


def test_upsert_keeps_origin_started_at_and_not_sent(monitor_conn):
    db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row()]))
    resumed = exchange_row(
        origin="proxy",
        started_at=400.0,
        last_at=400.2,
        outcome="delivered",
        not_sent=(),
        headline=None,
        connector_id=None,
    )
    db.write_batch(monitor_conn, WriteBatch(exchanges=[resumed]))

    row = monitor_conn.execute(
        "SELECT origin, started_at, last_at, outcome, not_sent, headline, connector_id FROM exchanges"
    ).fetchone()
    assert row == ("charger", 10.0, 400.2, "delivered", '["stats"]', "1.0 kWh", 1)
    assert scalar(monitor_conn, "SELECT count(*) FROM exchanges") == 1


def test_upsert_never_moves_last_at_back_nor_clears_incomplete(monitor_conn):
    db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row(last_at=50.0, incomplete=True)]))
    db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row(last_at=20.0, incomplete=False)]))

    assert monitor_conn.execute("SELECT last_at, incomplete FROM exchanges").fetchone() == (50.0, 1)


def test_frame_payload_goes_through_payload_blob_and_null_stays_null(monitor_conn):
    large = json.dumps({"a": "x" * payload_blob.COMPRESS_FROM})
    frames = [frame(payload=large), frame(payload=None, at=10.1)]
    db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row()], frames=frames))

    assert rows(monitor_conn, "SELECT payload FROM frames ORDER BY at") == [(payload_blob.encode(large),), (None,)]


def test_batch_writes_decisions_links_gaps_and_incomplete_marks(monitor_conn):
    db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row(1), exchange_row(2)]))
    db.write_batch(
        monitor_conn,
        WriteBatch(
            decisions=[DecisionRow(1, 10.1, "primary", "QUEUED", None)],
            links=[LinkRow("cp", "primary", 9.0, False, "dropped without close frame")],
            gaps=[Gap("cp", 9.5, 9.9, 37)],
            incomplete=[2],
        ),
    )

    assert rows(monitor_conn, "SELECT exchange_rowid, peer, reason FROM decisions") == [(1, "primary", "QUEUED")]
    assert rows(monitor_conn, "SELECT * FROM links") == [("cp", "primary", 9.0, 0, "dropped without close frame")]
    assert rows(monitor_conn, "SELECT * FROM monitor_gaps") == [("cp", 9.5, 9.9, 37)]
    assert rows(monitor_conn, "SELECT exchange_id FROM exchanges WHERE incomplete") == [(2,)]


def test_rows_beyond_one_statement_keep_their_batch_order(monitor_conn, monkeypatch):
    monkeypatch.setattr(db, "MAX_ROWS_PER_STATEMENT", 2)
    frames = [frame(n % 3 + 1, payload=f'{{"n":{n}}}', at=10.0) for n in range(7)]
    decisions = [DecisionRow(n % 3 + 1, 10.0, "primary", f"R{n}", None) for n in range(5)]

    db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row(n) for n in (1, 2, 3)], frames=frames))
    db.write_batch(monitor_conn, WriteBatch(decisions=decisions))

    blobs = rows(monitor_conn, "SELECT payload FROM frames ORDER BY rowid")
    assert [payload_blob.decode(blob) for (blob,) in blobs] == [{"n": n} for n in range(7)]
    assert rows(monitor_conn, "SELECT reason FROM decisions ORDER BY rowid") == [(f"R{n}",) for n in range(5)]
    assert scalar(monitor_conn, "SELECT count(*) FROM exchanges") == 3


def test_frames_and_decisions_of_an_exchange_not_stored_are_skipped(monitor_conn):
    db.write_batch(
        monitor_conn,
        WriteBatch(
            exchanges=[exchange_row(1)],
            frames=[frame(1), frame(2), frame(1, at=10.2)],
            decisions=[DecisionRow(2, 10.1, "primary", "QUEUED", None)],
        ),
    )

    assert rows(monitor_conn, "SELECT at FROM frames ORDER BY rowid") == [(10.0,), (10.2,)]
    assert scalar(monitor_conn, "SELECT count(*) FROM decisions") == 0


def test_a_failing_batch_writes_nothing(monitor_conn):
    bad = exchange_row(2, charger_id=None)
    with pytest.raises(sqlite3.IntegrityError):
        db.write_batch(monitor_conn, WriteBatch(exchanges=[exchange_row(1), bad]))

    assert scalar(monitor_conn, "SELECT count(*) FROM exchanges") == 0


def test_purge_chunk_deletes_old_rows_with_their_frames_in_chunks(monitor_conn):
    old = [exchange_row(n, last_at=float(n)) for n in range(1, 6)]
    db.write_batch(
        monitor_conn,
        WriteBatch(
            exchanges=[*old, exchange_row(99, last_at=1000.0)],
            frames=[frame(n) for n in (1, 2, 3, 4, 5, 99)],
            decisions=[DecisionRow(1, 1.0, "primary", "QUEUED", None)],
            links=[LinkRow("cp", "primary", 1.0, True), LinkRow("cp", "primary", 2000.0, False)],
            gaps=[Gap("cp", 1.0, 2.0, 1)],
        ),
    )

    assert db.purge_chunk(monitor_conn, cutoff=100.0, limit=3) == 5  # 3 exchanges, 1 link, 1 gap
    assert db.purge_chunk(monitor_conn, cutoff=100.0, limit=3) == 2
    assert db.purge_chunk(monitor_conn, cutoff=100.0, limit=3) == 0

    assert rows(monitor_conn, "SELECT exchange_id FROM exchanges") == [(99,)]
    assert scalar(monitor_conn, "SELECT count(*) FROM frames") == 1
    assert scalar(monitor_conn, "SELECT count(*) FROM decisions") == 0
    assert scalar(monitor_conn, "SELECT count(*) FROM links") == 1
