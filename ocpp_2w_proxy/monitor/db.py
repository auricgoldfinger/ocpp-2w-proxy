"""The monitor store: schema, migrations, batch writes and the retention purge.

Every function takes the connection and runs synchronously inside a SqliteWorker thread, never on the loop.
Reads live in ``queries.py``.
"""

from __future__ import annotations

import json
import sqlite3
import zlib

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

# Applied in order; PRAGMA user_version counts how many already ran. Append only, never edit.
MIGRATIONS: tuple[str, ...] = (_SCHEMA_V1,)

# A later write keeps what the first one decided: origin, started_at and not_sent.
_UPSERT_EXCHANGE = """
INSERT INTO exchanges (exchange_id, charger_id, origin, action, started_at, last_at, outcome, problem, incomplete,
                       transaction_id, connector_id, id_tag, not_sent, headline)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
_INSERT_FRAME = """
INSERT INTO frames (exchange_rowid, at, peer, dir, msg_type, wire_id, payload)
SELECT id, ?, ?, ?, ?, ?, ? FROM exchanges WHERE exchange_id = ?
"""
_INSERT_DECISION = """
INSERT INTO decisions (exchange_rowid, at, peer, reason, detail)
SELECT id, ?, ?, ?, ? FROM exchanges WHERE exchange_id = ?
"""
_INSERT_LINK = "INSERT INTO links (charger_id, peer, at, up) VALUES (?, ?, ?, ?)"
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
    """One transaction per batch. Compression happens here, in the worker thread, since zlib releases the GIL."""
    with conn:
        conn.executemany(
            _UPSERT_EXCHANGE,
            [(*row[:12], json.dumps(list(row.not_sent)), row.headline) for row in batch.exchanges],
        )
        conn.executemany(
            _INSERT_FRAME,
            [(*row[1:6], _compress(row.payload), row.exchange_id) for row in batch.frames],
        )
        conn.executemany(_INSERT_DECISION, [(*row[1:], row.exchange_id) for row in batch.decisions])
        conn.executemany(_INSERT_LINK, batch.links)
        conn.executemany(_INSERT_GAP, batch.gaps)
        conn.executemany(_MARK_INCOMPLETE, [(exchange_id,) for exchange_id in batch.incomplete])


def purge_chunk(conn: sqlite3.Connection, cutoff: float, limit: int = PURGE_CHUNK) -> int:
    """Delete at most ``limit`` exchanges (with their frames and decisions), links and gaps older than ``cutoff``.

    Returns the number of rows deleted from those tables; the caller repeats, one job per chunk, until 0.
    """
    with conn:
        return sum(conn.execute(purge, (cutoff, limit)).rowcount for purge in _PURGES)


def _compress(payload: str | None) -> bytes | None:
    return None if payload is None else zlib.compress(payload.encode())
