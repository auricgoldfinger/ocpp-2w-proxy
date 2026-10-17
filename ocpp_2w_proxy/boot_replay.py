"""Replay the charger's cached BootNotification to the primary on every (re)connection.

Some backends only accept calls from a charger that booted on this very connection. The
replay runs next to the link's reader, never in the way of attach(): a slow boot answer
must not fail the charger's connection. Nothing else goes out before it is answered, and
the charger's own identical boot then consumes the replayed answer so the primary does
not see two boots on one connection.

A link that comes back within ``grace`` seconds of losing a booted link skips the replay,
so a network blip does not look like a charger reboot to the primary (0 = always replay).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from .backend_link import BackendLink, BackendUnavailable
from .durations import format_duration
from .ocpp import Call, CallResult, Reply, is_accepted, new_message_id, now_iso
from .state import StateStore

logger = logging.getLogger(__name__)


class BootReplay:
    def __init__(
        self,
        store: StateStore,
        charger_id: str,
        call_timeout: float,
        grace: float = 0.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._store = store
        self._charger_id = charger_id
        self._call_timeout = call_timeout
        self._grace = grace
        self._clock = clock
        self._link: BackendLink | None = None
        # The primary accepted a boot on the current link (replayed, the charger's own, or inherited).
        self._booted = False
        self._booted_lost_at: float | None = None  # when the last booted link was lost
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

    def start(self, link: BackendLink) -> str | None:
        """Replay the boot on a new link, or skip it after a short outage; returns what was done."""
        self._link = link
        self._replayed = None
        if self._store.state.boot is None:
            self._done.set()
            return None
        if (outage := self._short_outage()) is not None:
            self._done.set()
            self._booted = True  # the primary still knows the charger from the link just lost
            logger.info("%s primary back after %s; boot replay skipped", self._charger_id, format_duration(outage))
            return f"boot replay skipped: down {format_duration(outage)}"
        self._done = asyncio.Event()
        self._task = asyncio.create_task(self._send(link), name="primary-boot-replay")
        return "boot replayed"

    def accepted(self, link: BackendLink) -> None:
        """The primary accepted the charger's own boot on ``link``."""
        if link is self._link:
            self._booted = True

    def _short_outage(self) -> float | None:
        """Seconds since the last booted link was lost, if within the grace period."""
        lost_at, self._booted_lost_at = self._booted_lost_at, None
        if self._grace <= 0 or lost_at is None:
            return None
        outage = self._clock() - lost_at
        return outage if outage <= self._grace else None

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
        self._booted_lost_at = self._clock() if self._booted else None
        self._booted = False
        self._link = None
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
            self._booted = True
            return
        logger.warning("%s did not accept the replayed BootNotification: %s", self._charger_id, reply)
