"""The hourly retention purge: rows older than ``retention_days`` leave the monitor store in small chunks.

Each chunk of at most 1000 exchanges (plus links and gaps) is its own job on the write worker, so the writer's
batches slip in between and never wait long behind a purge.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from ..log_throttle import LogThrottle
from .db import PURGE_CHUNK, purge_chunk
from .sqlite_worker import SqliteWorker

logger = logging.getLogger(__name__)

PURGE_INTERVAL = 3600.0
_DAY = 86_400.0


class RetentionPurge:
    def __init__(
        self,
        db: SqliteWorker,
        retention_days: int,
        *,
        interval: float = PURGE_INTERVAL,
        chunk: int = PURGE_CHUNK,
        clock: Callable[[], float] = time.time,
    ):
        self._db = db
        self._retention = retention_days * _DAY
        self._interval = interval
        self._chunk = chunk
        self._clock = clock
        self._error_log = LogThrottle()
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="monitor-retention")

    async def close(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.wait([self._task])

    async def purge(self) -> int:
        """Delete everything older than the retention period, one chunk per job; returns the rows deleted."""
        cutoff = self._clock() - self._retention
        total = 0
        while deleted := await self._db.run(purge_chunk, cutoff, self._chunk):
            total += deleted
            await asyncio.sleep(0)
        return total

    async def _run(self) -> None:
        while True:
            try:
                await self.purge()
            except Exception:  # sqlite3.Error above all; the next hour tries again
                if self._error_log.ready():
                    logger.exception("monitor retention purge failed")
            await asyncio.sleep(self._interval)
