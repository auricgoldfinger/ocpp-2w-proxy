"""A channel's long-running background task: started on demand, restarted if it ended,
stopped for good on close()."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any


class RestartableWorker:
    def __init__(self, run: Callable[[], Coroutine[Any, Any, None]], name: str):
        self._run = run
        self._name = name
        self._task: asyncio.Task | None = None
        self.closed = False

    def ensure_running(self) -> None:
        if not self.closed and (self._task is None or self._task.done()):
            self._task = asyncio.create_task(self._run(), name=self._name)

    async def close(self) -> None:
        self.closed = True
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
