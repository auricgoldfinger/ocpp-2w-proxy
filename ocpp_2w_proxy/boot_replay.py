"""Replay the charger's cached BootNotification to the primary on every (re)connection.

Some backends only accept calls from a charger that booted on this very connection. The
replay runs next to the link's reader, never in the way of attach(): a slow boot answer
must not fail the charger's connection. Nothing else goes out before it is answered, and
the charger's own identical boot then consumes the replayed answer so the primary does
not see two boots on one connection.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from .backend_link import BackendLink, BackendUnavailable
from .ocpp import Call, CallResult, Reply, is_accepted, new_message_id, now_iso
from .state import StateStore

logger = logging.getLogger(__name__)


class BootReplay:
    def __init__(self, store: StateStore, charger_id: str, call_timeout: float):
        self._store = store
        self._charger_id = charger_id
        self._call_timeout = call_timeout
        # Set once the cached boot has been replayed on the current link (or needs no replay).
        self._done = asyncio.Event()
        self._done.set()
        self._task: asyncio.Task | None = None
        # (payload, reply) of the replayed boot until the charger's own identical boot consumes it.
        self._replayed: tuple[dict, CallResult] | None = None

    def remember(self, call: Call) -> None:
        """Cache the charger's boot: the next reconnect replays it to the primary."""
        self._store.state.boot = call.payload
        self._store.save()

    def start(self, link: BackendLink) -> None:
        if self._store.state.boot is None:
            self._done.set()
            return
        self._done = asyncio.Event()
        self._replayed = None
        self._task = asyncio.create_task(self._send(link), name="primary-boot-replay")

    async def wait(self, timeout: float) -> bool:
        """True once the replay is over, False if it is still pending after `timeout`."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._done.wait(), timeout)
        return self._done.is_set()

    async def wait_unbounded(self) -> None:
        await self._done.wait()

    def consume(self, call: Call) -> Reply | None:
        """The charger's boot matches the one just replayed on this connection: answer with
        the primary's earlier reply (fresh clock) instead of booting twice."""
        replayed, self._replayed = self._replayed, None
        if replayed is None or replayed[0] != call.payload:
            return None
        return CallResult(call.id, {**replayed[1].payload, "currentTime": now_iso()})

    async def stop(self) -> None:
        """The link is gone: abandon the replay and release anything waiting on it."""
        self._replayed = None
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
        self._done.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def _send(self, link: BackendLink) -> None:
        boot = self._store.state.boot
        try:
            reply = await link.call(Call(new_message_id(), "BootNotification", boot), self._call_timeout)
        except TimeoutError, BackendUnavailable:
            logger.warning("%s did not confirm the replayed BootNotification; continuing anyway", self._charger_id)
            return
        finally:
            self._done.set()
        if is_accepted(reply):
            self._replayed = (boot, reply)
            return
        logger.warning("%s did not accept the replayed BootNotification: %s", self._charger_id, reply)
