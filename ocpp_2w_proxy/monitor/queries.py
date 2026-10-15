"""Read queries behind the monitor API. Each runs synchronously in the reader's SqliteWorker thread, inside one
short transaction under a 250 ms time limit, and returns the response body as JSON bytes built in that thread.

Exchange ids appear as strings: they can exceed the integers JavaScript represents safely.

``rebuild_projections`` is the one read the writer makes, in its own thread, for exchanges it continues after a
restart or after evicting them from memory.
"""

from __future__ import annotations

import json
import sqlite3
import time
import zlib
from collections.abc import Callable, Collection, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from .meter_values import ENERGY, latest_reading, number
from .projection import DEFAULT_STALE_AFTER, ExchangeProjection
from .replay import StoredDecision, StoredExchange, StoredFrame, decision_views, frame_views, origin_payload, project

type Clock = Callable[[], float]

READ_TIME_LIMIT = 0.25
_PROGRESS_STEPS = 1000  # SQLite VM instructions between deadline checks
MAX_LIMIT = 200
HEARTBEAT = "Heartbeat"
_CHARGER = "charger"


class InvalidCursor(ValueError):
    pass


@dataclass(frozen=True)
class ExchangeQuery:
    """The filters of ``GET /api/exchanges``, already parsed."""

    charger: str | None = None
    action: str | None = None
    outcome: str | None = None
    peer: str | None = None
    problem: bool = False
    tx: int | None = None
    q: str | None = None
    exclude: tuple[str, ...] = ()
    since: float | None = None
    before: str | None = None
    limit: int = MAX_LIMIT


# --- reads -------------------------------------------------------------------------------


def list_exchanges(
    conn: sqlite3.Connection,
    query: ExchangeQuery,
    *,
    now: float | None = None,
    stale_after: float = DEFAULT_STALE_AFTER,
    clock: Clock = time.monotonic,
) -> bytes:
    """``{"exchanges": [summary, ...], "next": cursor | null, "hidden": {"Heartbeat": n}}``, newest first."""
    limit = min(max(query.limit, 1), MAX_LIMIT)
    filters, params = _filters(query)
    page_filters, page_params = _page_filters(query)
    with _reading(conn, clock):
        rows = conn.execute(
            f"SELECT {_EXCHANGE_COLUMNS} FROM exchanges e {_where(filters + page_filters)}"
            " ORDER BY e.started_at DESC, e.id DESC LIMIT ?",
            (*params, *page_params, limit + 1),
        ).fetchall()
        page = rows[:limit]
        summaries = [project(stored, stale_after).summary(now) for stored in _load(conn, page)]
        hidden = {}
        if HEARTBEAT in query.exclude:
            hidden[HEARTBEAT] = conn.execute(
                f"SELECT count(*) FROM exchanges e {_where([*filters, 'e.action = ?'])}", (*params, HEARTBEAT)
            ).fetchone()[0]
    next_cursor = _cursor(page[-1]) if len(rows) > limit else None
    return _json({"exchanges": summaries, "next": next_cursor, "hidden": hidden})


def exchange_detail(
    conn: sqlite3.Connection,
    exchange_id: int,
    *,
    now: float | None = None,
    stale_after: float = DEFAULT_STALE_AFTER,
    clock: Clock = time.monotonic,
) -> bytes | None:
    """The summary plus the origin payload, every frame (payload filled in) and every decision; None if unknown."""
    with _reading(conn, clock):
        row = conn.execute(
            f"SELECT {_EXCHANGE_COLUMNS} FROM exchanges e WHERE e.exchange_id = ?", (exchange_id,)
        ).fetchone()
        if row is None:
            return None
        (stored,) = _load(conn, [row])
    detail = project(stored, stale_after).summary(now)
    detail["payload"] = origin_payload(stored.frames)
    detail["frames"] = frame_views(stored.frames)
    detail["decisions"] = decision_views(stored.decisions)
    return _json(detail)


def session_summary(conn: sqlite3.Connection, transaction_id: int, *, clock: Clock = time.monotonic) -> bytes | None:
    """The aggregate of one primary transaction for the session header; None when no exchange names it."""
    with _reading(conn, clock):
        charger_id, started_at, exchanges, problems = conn.execute(
            "SELECT charger_id, min(started_at), count(*), coalesce(sum(problem), 0) FROM exchanges"
            " WHERE transaction_id = ?",
            (transaction_id,),
        ).fetchone()
        if not exchanges:
            return None
        start = _latest(conn, transaction_id, "StartTransaction")
        stop = _latest(conn, transaction_id, "StopTransaction")
        meter = _latest(conn, transaction_id, "MeterValues")
        start_payload, stop_payload, meter_payload = (_origin_payload_of(conn, row) for row in (start, stop, meter))
    meter_start = number(start_payload.get("meterStart"))
    meter_end = number(stop_payload.get("meterStop"))
    if meter_end is None:
        meter_end = latest_reading(meter_payload, ENERGY)
    return _json(
        {
            "transaction_id": transaction_id,
            "charger_id": charger_id,
            "connector_id": start[3] if start else None,
            "id_tag": start[4] if start else None,
            "started_at": start[1] if start else started_at,
            "stopped_at": stop[1] if stop else None,
            "energy_wh": None if meter_start is None or meter_end is None else round(meter_end - meter_start),
            "exchanges": exchanges,
            "problems": problems,
        }
    )


def links_report(
    conn: sqlite3.Connection, snapshot: Mapping[str, Any], since: float, *, clock: Clock = time.monotonic
) -> bytes:
    """The live ``snapshot`` (taken on the loop from the channels) plus link history and gaps since ``since``."""
    with _reading(conn, clock):
        history = conn.execute(
            "SELECT charger_id, peer, at, up FROM links WHERE at >= ? ORDER BY at, rowid", (since,)
        ).fetchall()
        gaps = conn.execute(
            "SELECT charger_id, from_at, to_at, dropped FROM monitor_gaps WHERE to_at >= ? ORDER BY from_at, rowid",
            (since,),
        ).fetchall()
    return _json(
        {
            **snapshot,
            "history": [{"charger_id": c, "peer": p, "at": at, "up": bool(up)} for c, p, at, up in history],
            "gaps": [{"charger_id": c, "from_at": f, "to_at": t, "dropped": d} for c, f, t, d in gaps],
        }
    )


def rebuild_projections(
    conn: sqlite3.Connection, exchange_ids: Collection[int], stale_after: float = DEFAULT_STALE_AFTER
) -> list[ExchangeProjection]:
    """The projections of those ``exchange_ids`` that are stored, rebuilt from their rows; unknown ids are skipped."""
    if not exchange_ids:
        return []
    marks = ", ".join("?" * len(exchange_ids))
    rows = conn.execute(
        f"SELECT {_EXCHANGE_COLUMNS} FROM exchanges e WHERE e.exchange_id IN ({marks})", tuple(exchange_ids)
    ).fetchall()
    return [project(stored, stale_after) for stored in _load(conn, rows)]


# --- time limit and transaction ----------------------------------------------------------


@contextmanager
def _reading(conn: sqlite3.Connection, clock: Clock) -> Iterator[None]:
    """One short read transaction; a query still running after READ_TIME_LIMIT is interrupted."""
    deadline = clock() + READ_TIME_LIMIT
    conn.set_progress_handler(lambda: clock() > deadline, _PROGRESS_STEPS)
    try:
        conn.execute("BEGIN")
        try:
            yield
        finally:
            conn.rollback()
    finally:
        conn.set_progress_handler(None, 0)


# --- exchange filters --------------------------------------------------------------------

_EXCHANGE_COLUMNS = "e.id, e.exchange_id, e.charger_id, e.origin, e.action, e.started_at, e.incomplete, e.not_sent"

_EQUALS: tuple[tuple[str, str], ...] = (
    ("charger", "e.charger_id = ?"),
    ("action", "e.action = ?"),
    ("outcome", "e.outcome = ?"),
    ("tx", "e.transaction_id = ?"),
    ("since", "e.started_at >= ?"),
)

# A leg to a peer: a frame with it, or a decision about it. A decision about a backend command's own origin
# ends the command's charger leg (see ExchangeProjection).
_PEER = (
    "e.origin IS NOT ? AND ("
    " EXISTS (SELECT 1 FROM frames f WHERE f.exchange_rowid = e.id AND f.peer = ?)"
    " OR EXISTS (SELECT 1 FROM decisions d WHERE d.exchange_rowid = e.id"
    f"  AND (d.peer = ? OR (? = '{_CHARGER}' AND d.peer = e.origin))))"
)
# Every placeholder takes the same LIKE pattern; positional "?" keeps the clauses composable.
_SEARCH = (
    "(CAST(e.exchange_id AS TEXT) LIKE ? ESCAPE '\\' OR e.id_tag LIKE ? ESCAPE '\\'"
    " OR CAST(e.transaction_id AS TEXT) LIKE ? ESCAPE '\\'"
    " OR EXISTS (SELECT 1 FROM frames f WHERE f.exchange_rowid = e.id AND f.wire_id LIKE ? ESCAPE '\\'))"
)


def _filters(query: ExchangeQuery) -> tuple[list[str], list[Any]]:
    """Every filter except the page-only ones; the hidden Heartbeat count uses exactly these."""
    clauses: list[str] = []
    params: list[Any] = []
    for name, clause in _EQUALS:
        value = getattr(query, name)
        if value is not None:
            clauses.append(clause)
            params.append(value)
    if query.problem:
        clauses.append("e.problem")
    if query.peer is not None:
        clauses.append(_PEER)
        params.extend([query.peer] * _PEER.count("?"))
    if query.q:
        clauses.append(_SEARCH)
        params.extend([f"%{_escape_like(query.q)}%"] * _SEARCH.count("?"))
    return clauses, params


def _page_filters(query: ExchangeQuery) -> tuple[list[str], list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.exclude:
        clauses.append(f"(e.action IS NULL OR e.action NOT IN ({', '.join('?' * len(query.exclude))}))")
        params.extend(query.exclude)
    if query.before is not None:
        clauses.append("(e.started_at, e.id) < (?, ?)")
        params.extend(_parse_cursor(query.before))
    return clauses, params


def _where(clauses: Sequence[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _cursor(row: tuple) -> str:
    rowid, _, _, _, _, started_at, *_ = row
    return f"{started_at!r}:{rowid}"


def _parse_cursor(cursor: str) -> tuple[float, int]:
    try:
        started_at, rowid = cursor.split(":")
        return float(started_at), int(rowid)
    except ValueError as exc:
        raise InvalidCursor(f"invalid cursor {cursor!r}") from exc


# --- loading stored exchanges ------------------------------------------------------------


def _load(conn: sqlite3.Connection, rows: Sequence[tuple]) -> list[StoredExchange]:
    """The exchanges of ``rows`` with their frames and decisions, fetched in one query each."""
    rowids = [row[0] for row in rows]
    frames: dict[int, list[StoredFrame]] = {rowid: [] for rowid in rowids}
    decisions: dict[int, list[StoredDecision]] = {rowid: [] for rowid in rowids}
    if rowids:
        marks = ", ".join("?" * len(rowids))
        for rowid, *frame in conn.execute(
            "SELECT exchange_rowid, at, peer, dir, msg_type, wire_id, payload FROM frames"
            f" WHERE exchange_rowid IN ({marks}) ORDER BY exchange_rowid, at, rowid",
            rowids,
        ):
            frames[rowid].append(StoredFrame(*frame[:5], _decompress(frame[5])))
        for rowid, *decision in conn.execute(
            "SELECT exchange_rowid, at, peer, reason, detail FROM decisions"
            f" WHERE exchange_rowid IN ({marks}) ORDER BY exchange_rowid, at, rowid",
            rowids,
        ):
            decisions[rowid].append(StoredDecision(*decision))
    return [
        StoredExchange(
            exchange_id,
            charger_id,
            origin,
            action,
            started_at,
            bool(incomplete),
            tuple(json.loads(not_sent)),
            frames[rowid],
            decisions[rowid],
        )
        for rowid, exchange_id, charger_id, origin, action, started_at, incomplete, not_sent in rows
    ]


def _latest(conn: sqlite3.Connection, transaction_id: int, action: str) -> tuple | None:
    return conn.execute(
        "SELECT id, started_at, last_at, connector_id, id_tag FROM exchanges"
        " WHERE transaction_id = ? AND action = ? ORDER BY started_at DESC, id DESC LIMIT 1",
        (transaction_id, action),
    ).fetchone()


def _origin_payload_of(conn: sqlite3.Connection, row: tuple | None) -> dict[str, Any]:
    if row is None:
        return {}
    blob = conn.execute(
        "SELECT payload FROM frames WHERE exchange_rowid = ? AND msg_type = 'call' AND payload IS NOT NULL"
        " ORDER BY at, rowid LIMIT 1",
        (row[0],),
    ).fetchone()
    payload = None if blob is None else _decompress(blob[0])
    return payload if isinstance(payload, dict) else {}


def _decompress(blob: bytes | None) -> Any:
    return None if blob is None else json.loads(zlib.decompress(blob))


def _json(body: object) -> bytes:
    return json.dumps(body, separators=(",", ":")).encode()
