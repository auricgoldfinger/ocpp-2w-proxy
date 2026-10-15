"""One SQLite connection bound to its own thread, driven from the event loop.

sqlite3 and zlib release the GIL, so the work runs truly beside the loop. Each worker has a dedicated
single-thread executor: the loop's default executor also serves ``getaddrinfo`` for backend reconnects and
must never queue behind a database job.
"""

from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Concatenate


class SqliteWorker:
    def __init__(self, path: Path | str, *, readonly: bool):
        self._path = Path(path)
        self._readonly = readonly
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"monitor-sqlite-{_role(readonly)}")
        self._conn: sqlite3.Connection | None = None

    async def run[*Ts, R](self, fn: Callable[Concatenate[sqlite3.Connection, *Ts], R], *args: *Ts) -> R:
        """``fn(conn, *args)`` in the worker thread; the connection is opened there on first use."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._call, fn, args)

    async def close(self) -> None:
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(self._executor, self._close_connection)
        finally:
            self._executor.shutdown(wait=False)

    def _call(self, fn: Callable[..., object], args: tuple) -> object:
        if self._conn is None:
            self._conn = self._connect()
        return fn(self._conn, *args)

    def _connect(self) -> sqlite3.Connection:
        if self._readonly:
            return sqlite3.connect(f"{self._path.resolve().as_uri()}?mode=ro", uri=True)
        return sqlite3.connect(self._path)

    def _close_connection(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def _role(readonly: bool) -> str:
    return "reader" if readonly else "writer"
