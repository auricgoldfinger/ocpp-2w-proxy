"""The monitor store: schema, migrations, batch writes and the retention purge.

Every function takes the connection and runs synchronously inside a SqliteWorker thread, never on the loop.
Reads live in ``queries.py``.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from . import payload_blob
from .rows import WriteBatch

# No auto_vacuum: with fixed retention freed pages are reused, so the file size levels off.
_PRAGMAS = (
    "PRAGMA auto_vacuum = NONE",
    "PRAGMA journal_mode = WAL",
    "PRAGMA synchronous = NORMAL",
    "PRAGMA foreign_keys = ON",
)

_SCHEMA_V1 = """
CREATE TABLE exchanges (
    id INTEGER PRIMARY KEY,
    exchange_id INTEGER NOT NULL UNIQUE,
    charger_id TEXT NOT NULL,
    origin TEXT,
    action TEXT,
    started_at REAL NOT NULL,
    last_at REAL NOT NULL,
    outcome TEXT,
    problem INTEGER NOT NULL DEFAULT 0,
    incomplete INTEGER NOT NULL DEFAULT 0,
    transaction_id INTEGER,
    connector_id INTEGER,
    id_tag TEXT,
    not_sent TEXT NOT NULL DEFAULT '[]',
    headline TEXT
);
CREATE INDEX exchanges_charger_started ON exchanges (charger_id, started_at DESC);
CREATE INDEX exchanges_started ON exchanges (started_at);
CREATE INDEX exchanges_transaction ON exchanges (transaction_id);
CREATE INDEX exchanges_problem ON exchanges (started_at) WHERE problem;

CREATE TABLE frames (
    exchange_rowid INTEGER NOT NULL REFERENCES exchanges (id) ON DELETE CASCADE,
    at REAL NOT NULL,
    peer TEXT NOT NULL,
    dir TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    wire_id TEXT NOT NULL,
    payload BLOB
);
CREATE INDEX frames_exchange ON frames (exchange_rowid);

CREATE TABLE decisions (
    exchange_rowid INTEGER NOT NULL REFERENCES exchanges (id) ON DELETE CASCADE,
    at REAL NOT NULL,
    peer TEXT NOT NULL,
    reason TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX decisions_exchange ON decisions (exchange_rowid);

CREATE TABLE links (
    charger_id TEXT NOT NULL,
    peer TEXT NOT NULL,
    at REAL NOT NULL,
    up INTEGER NOT NULL
);

CREATE TABLE monitor_gaps (
    charger_id TEXT NOT NULL,
    from_at REAL NOT NULL,
    to_at REAL NOT NULL,
    dropped INTEGER NOT NULL
);
CREATE INDEX monitor_gaps_charger ON monitor_gaps (charger_id, from_at);
"""

# Why a link went down (who closed it), or how it came up (whether the primary's boot was replayed).
_LINK_NOTES_V2 = "ALTER TABLE links ADD COLUMN note TEXT;"

# Applied in order; PRAGMA user_version counts how many already ran. Append only, never edit.
MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1, _LINK_NOTES_V2)

# A later write keeps what the first one decided: origin, started_at and not_sent.
_UPSERT_EXCHANGES = """
INSERT INTO exchanges (exchange_id, charger_id, origin, action, started_at, last_at, outcome, problem, incomplete,
                       transaction_id, connector_id, id_tag, not_sent, headline)
VALUES {values}
ON CONFLICT (exchange_id) DO UPDATE SET
    action = coalesce(exchanges.action, excluded.action),
    last_at = max(exchanges.last_at, excluded.last_at),
    outcome = excluded.outcome,
    problem = excluded.problem,
    incomplete = exchanges.incomplete OR excluded.incomplete,
    transaction_id = coalesce(excluded.transaction_id, exchanges.transaction_id),
    connector_id = coalesce(excluded.connector_id, exchanges.connector_id),
    id_tag = coalesce(excluded.id_tag, exchanges.id_tag),
    headline = coalesce(excluded.headline, exchanges.headline)
"""
# column1 keeps the batch order; a row whose exchange is not stored is skipped.
_INSERT_FRAMES = """
INSERT INTO frames (exchange_rowid, at, peer, dir, msg_type, wire_id, payload)
SELECT e.id, v.column2, v.column3, v.column4, v.column5, v.column6, v.column7
FROM (VALUES {values}) AS v JOIN exchanges AS e ON e.exchange_id = v.column8
ORDER BY v.column1
"""
_INSERT_DECISIONS = """
INSERT INTO decisions (exchange_rowid, at, peer, reason, detail)
SELECT e.id, v.column2, v.column3, v.column4, v.column5
FROM (VALUES {values}) AS v JOIN exchanges AS e ON e.exchange_id = v.column6
ORDER BY v.column1
"""
MAX_ROWS_PER_STATEMENT = 500
_INSERT_LINK = "INSERT INTO links (charger_id, peer, at, up, note) VALUES (?, ?, ?, ?, ?)"
_INSERT_GAP = "INSERT INTO monitor_gaps (charger_id, from_at, to_at, dropped) VALUES (?, ?, ?, ?)"
_MARK_INCOMPLETE = "UPDATE exchanges SET incomplete = 1 WHERE exchange_id = ?"

PURGE_CHUNK = 1000
_PURGES = (
    "DELETE FROM exchanges WHERE id IN (SELECT id FROM exchanges WHERE last_at < ? ORDER BY id LIMIT ?)",
    "DELETE FROM links WHERE rowid IN (SELECT rowid FROM links WHERE at < ? ORDER BY rowid LIMIT ?)",
    "DELETE FROM monitor_gaps WHERE rowid IN (SELECT rowid FROM monitor_gaps WHERE to_at < ? ORDER BY rowid LIMIT ?)",
)


def prepare_writer(conn: sqlite3.Connection) -> None:
    """Pragmas for the one writing connection, then the schema up to date."""
    for pragma in _PRAGMAS:
        conn.execute(pragma)
    migrate(conn)


def migrate(conn: sqlite3.Connection) -> None:
    (version,) = conn.execute("PRAGMA user_version").fetchone()
    for number, script in enumerate(MIGRATIONS[version:], start=version + 1):
        conn.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;")


def write_batch(conn: sqlite3.Connection, batch: WriteBatch) -> None:
    """One transaction per batch, in the worker thread; large payloads are compressed here too (``payload_blob``).

    Exchanges, frames and decisions go in as multi-row statements, not ``executemany``: every row of the latter
    is a step that releases the GIL and must take it back, and each time the worker takes it back the loop can
    stall behind it. A few statements per batch keep the worker's share of the GIL short.
    """
    with conn:
        _insert_rows(
            conn,
            _UPSERT_EXCHANGES,
            [(*row[:12], json.dumps(list(row.not_sent)), row.headline) for row in batch.exchanges],
        )
        _insert_rows(
            conn,
            _INSERT_FRAMES,
            [(seq, *row[1:6], _compress(row.payload), row.exchange_id) for seq, row in enumerate(batch.frames)],
        )
        _insert_rows(
            conn, _INSERT_DECISIONS, [(seq, *row[1:], row.exchange_id) for seq, row in enumerate(batch.decisions)]
        )
        conn.executemany(_INSERT_LINK, batch.links)
        conn.executemany(_INSERT_GAP, batch.gaps)
        conn.executemany(_MARK_INCOMPLETE, [(exchange_id,) for exchange_id in batch.incomplete])


def _insert_rows(conn: sqlite3.Connection, template: str, rows: Sequence[tuple]) -> None:
    """``template`` with its ``{values}`` filled by as many rows per statement as SQLite's variable limit allows."""
    if not rows:
        return
    width = len(rows[0])
    placeholder = f"({','.join('?' * width)})"
    per_statement = max(1, min(MAX_ROWS_PER_STATEMENT, conn.getlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER) // width))
    for start in range(0, len(rows), per_statement):
        chunk = rows[start : start + per_statement]
        conn.execute(template.format(values=",".join([placeholder] * len(chunk))), [v for row in chunk for v in row])


def purge_chunk(conn: sqlite3.Connection, cutoff: float, limit: int = PURGE_CHUNK) -> int:
    """Delete at most ``limit`` exchanges (with their frames and decisions), links and gaps older than ``cutoff``.

    Returns the number of rows deleted from those tables; the caller repeats, one job per chunk, until 0.
    """
    with conn:
        return sum(conn.execute(purge, (cutoff, limit)).rowcount for purge in _PURGES)


def _compress(payload: str | None) -> bytes | None:
    return None if payload is None else payload_blob.encode(payload)
