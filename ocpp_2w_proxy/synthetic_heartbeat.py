"""Heartbeats the proxy invents for a backend that does not receive the charger's own.

A backend states the interval it wants in its BootNotification.conf; without a heartbeat
at that rate it would time the idle connection out.
"""

from __future__ import annotations

import asyncio

from .backend_link import BackendLink, BackendUnavailable
from .ocpp import Call, new_message_id


class SyntheticHeartbeat:
    def __init__(self, backend_name: str, call_timeout: float):
        self._backend_name = backend_name
        self._call_timeout = call_timeout
        self.interval = 0  # seconds; 0 until the backend has stated one
        self._task: asyncio.Task | None = None

    def start(self, link: BackendLink) -> None:
        """Begin beating on `link` once the interval is known; a no-op if already beating."""
        if not self.interval or self._task is not None:
            return
        self._task = asyncio.create_task(self._beat(link), name=f"secondary-heartbeat-{self._backend_name}")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def _beat(self, link: BackendLink) -> None:
        while True:
            await asyncio.sleep(self.interval)
            try:
                await link.call(Call(new_message_id(), "Heartbeat", {}), self._call_timeout)
            except TimeoutError:
                continue  # no answer: try again at the next interval
            except BackendUnavailable:
                return  # the link is gone; the channel tears everything down
