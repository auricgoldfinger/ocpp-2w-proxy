"""Primary Backend connection and durable delivery for selected charger messages."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from websockets.exceptions import InvalidHandshake, InvalidURI

from .backend_auth import MissingChargerCredentials, backend_headers, backend_url
from .backend_link import BackendLink, BackendUnavailable, send_reply
from .backoff import Backoff
from .charger_auth import ChargerIdentity
from .config import AuthMode, BackendConfig, ChargerConfig
from .ocpp import Call, CallError, CallResult, Reply, new_message_id, now_iso, to_dict
from .state import StateStore, restore_outbox
from .traffic_log import TrafficLog

logger = logging.getLogger(__name__)

QUEUED_ACTIONS = frozenset({"StatusNotification", "MeterValues", "StopTransaction"})
# Dropped first when the queue overflows: meter data is expendable, stops are not.
EXPENDABLE_ACTION = "MeterValues"

OnCall = Callable[[Call], Awaitable[None]]


class PrimaryChannel:
    """Reconnects to one primary and delivers queued calls independently of a charger socket.

    Invariant: a link registered in _link always has a live reader task, so sessions and
    queued delivery only ever see links whose replies are being read.
    """

    def __init__(
        self,
        backend: BackendConfig,
        charger: ChargerConfig,
        store: StateStore,
        traffic: TrafficLog,
    ):
        self.name = backend.name
        self._backend = backend
        self._charger = charger
        self._store = store
        self._traffic = traffic
        self._max_queue = backend.max_queue
        self._identity = ChargerIdentity(charger, None, None)
        # A session is attached exactly while _on_call is set. The generation ties that routing
        # to the latest attach, so a replaced session that outlives the server's replacement
        # timeout cannot unhook its successor during cleanup (UC-002 BR-004).
        self._on_call: OnCall | None = None
        self._generation = 0
        self._link: BackendLink | None = None
        self._reader: asyncio.Task | None = None
        self._queue: deque[Call] = deque(call for call, _ in restore_outbox(store.state.primary_outbox))
        self._in_flight: Call | None = None  # the queued head whose reply the drain awaits
        self._queue_changed = asyncio.Event()
        self._wake = asyncio.Event()
        self._connect_lock = asyncio.Lock()
        self._worker: asyncio.Task | None = None
        self._closed = False

    async def attach(self, identity: ChargerIdentity, on_call: OnCall) -> int:
        """Open the primary for a new session; fail this connection if the initial open fails.

        _connect() hands out only links with a live reader, so the new session's calls are
        answered rather than sent into a socket nobody reads (UC-002 A4). The returned token
        identifies this attach: hand it to detach() to end this session's routing.
        """
        self._identity = identity
        self._on_call = on_call
        self._generation += 1
        token = self._generation
        self._queue_changed.set()
        self._wake.set()
        self._ensure_worker()
        try:
            await self._connect()
        except OSError, TimeoutError, InvalidHandshake, InvalidURI, MissingChargerCredentials:
            self._release(token)
            raise
        return token

    def detach(self, token: int) -> None:
        """End charger-facing command routing while allowing the durable outbox to drain.

        Only the session whose attach() returned `token` may unhook the channel: a replaced
        session that outlives REPLACE_TIMEOUT_SECONDS still runs this cleanup, but it must
        not take the newer session's routing down with it (UC-002 BR-004).
        """
        self._release(token)

    def _release(self, token: int) -> None:
        if token != self._generation:
            return  # a newer session owns the channel now
        self._on_call = None
        self._queue_changed.set()
        self._wake.set()

    def start_background(self) -> None:
        """Drain restored messages at server startup when credentials are available."""
        if self._queue:
            self._ensure_worker()

    async def call(self, call: Call, timeout: float) -> Reply:
        # While the outbox drains, StatusNotification/MeterValues/StopTransaction
        # wait their turn: sent live they would overtake older queued messages and
        # the primary would see a new status before the previous transaction's stop.
        if self._link is None or (call.action in QUEUED_ACTIONS and self._queue):
            return self._unavailable(call)
        link = self._link
        try:
            return await link.call(call, timeout)
        except BackendUnavailable:
            return self._unavailable(call)
        except TimeoutError:
            # A wedged link is useless: closing it makes the worker reconnect (UC-003 A1).
            await link.close()
            return self._unavailable(call)

    async def reply(self, message: Reply) -> None:
        await send_reply(self._link, message, self.name)

    async def close(self) -> None:
        self._closed = True
        self._queue_changed.set()
        worker = self._worker
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        await self._discard(self._link)

    def _unavailable(self, call: Call) -> Reply:
        if call.action == "Heartbeat":
            # The charger only asks to sync its clock; answer it locally instead of an
            # error, so an outage does not make the charger treat it as a protocol fault.
            logger.warning("%s answered Heartbeat locally while the primary backend is unavailable", self._charger.id)
            return CallResult(call.id, {"currentTime": now_iso()})
        if call.action in QUEUED_ACTIONS:
            self._queue.append(call)
            self._enforce_queue_limit()
            self._persist_queue()
            self._queue_changed.set()
            self._ensure_worker()
            logger.warning("%s queued %s while the primary backend is unavailable", self._charger.id, call.action)
            return CallResult(call.id, {})
        logger.warning("%s primary unavailable; refusing %s", self._charger.id, call.action)
        return CallError(call.id, "GenericError", "primary backend unavailable")

    def _enforce_queue_limit(self) -> None:
        while len(self._queue) > self._max_queue:
            victim = self._sacrifice()
            if victim is None:
                break  # only the in-flight head is over the limit; its confirmation pops it
            self._queue.remove(victim)
            logger.error("primary queue full (%d); dropped queued %s", self._max_queue, victim.action)

    def _sacrifice(self) -> Call | None:
        """The first droppable call: meter data first, oldest otherwise. Never the
        head currently being sent - removing it would make the drain pop the next
        call, which was never sent, losing it silently."""
        candidates = [c for c in self._queue if c is not self._in_flight]
        if not candidates:
            return None
        return next((c for c in candidates if c.action == EXPENDABLE_ACTION), candidates[0])

    def _persist_queue(self) -> None:
        self._store.state.primary_outbox = [to_dict(call) for call in self._queue]
        self._store.save()

    def _ensure_worker(self) -> None:
        if not self._closed and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._run(), name=f"primary-{self._charger.id}")

    async def _connect(self) -> BackendLink:
        async with self._connect_lock:
            if self._link is not None:
                return self._link
            link = await BackendLink.open(
                self._backend.name,
                backend_url(self._backend, self._charger.primary_id),
                backend_headers(self._backend, self._charger.primary_id, self._identity),
                self._identity.user_agent,
                self._traffic,
            )
            self._reader = asyncio.create_task(link.serve(self._dispatch_call), name="primary-reader")
            self._link = link
            return link

    async def _discard(self, link: BackendLink | None) -> None:
        """Deregister one link and stop its reader; its in-flight calls have failed (UC-003 A1)."""
        if link is None or self._link is not link:
            return
        self._link = None
        reader = self._reader
        self._reader = None
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        # Shielded: cancelling the worker here (server shutdown) must still leave the link closed.
        await asyncio.shield(link.close())

    async def _run(self) -> None:
        backoff = Backoff()
        while not self._closed:
            if self._link is None:
                if self._on_call is None and not self._queue:
                    self._queue_changed.clear()
                    await self._queue_changed.wait()
                    continue
                if self._backend.auth is AuthMode.FORWARD and self._identity.authorization_header is None:
                    # The charger's credentials are never persisted; wait for its next handshake.
                    self._queue_changed.clear()
                    await self._queue_changed.wait()
                    continue
                try:
                    await self._connect()
                except (OSError, TimeoutError, InvalidHandshake, InvalidURI, MissingChargerCredentials) as exc:
                    logger.warning(
                        "%s primary backend unreachable (%s); retrying in ~%.0fs",
                        self._charger.id,
                        exc,
                        backoff.current,
                    )
                    await self._backoff_sleep(backoff.next_delay())
                    continue
                backoff.reset()

            link = self._link
            if link is None:
                continue
            dead = True
            try:
                dead = await self._pump(link)
            except Exception:
                logger.exception("%s primary connection failed unexpectedly", self._charger.id)
            # Never discard a healthy link a session just attached to: without its reader,
            # every call of that session would time out against the primary (UC-002 A4).
            if dead or (self._on_call is None and not self._queue):
                await self._discard(link)
                if dead and (self._on_call is not None or self._queue):
                    await self._backoff_sleep(backoff.next_delay())
            # A parked link stays registered and is pumped again on the next loop turn.

    async def _backoff_sleep(self, delay: float) -> None:
        """Wait out a retry delay, but continue at once when a new session attaches."""
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), delay)
        self._wake.clear()

    async def _pump(self, link: BackendLink) -> bool:
        """Watch one registered link until it drops or the outbox parks it.

        Returns True if the link died; False if it was parked healthy with no session and
        nothing queued, so the caller decides whether to keep it.
        """
        reader = self._reader
        sender = asyncio.create_task(self._drain(link), name="primary-outbox")
        try:
            done, _ = await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                if task.cancelled():
                    continue
                exc = task.exception()
                if exc and not isinstance(exc, BackendUnavailable):
                    logger.error("%s primary channel task failed: %r", self._charger.id, exc)
            parked = sender in done and not sender.cancelled() and sender.exception() is None and reader not in done
            return not parked
        finally:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)

    async def _dispatch_call(self, call: Call) -> None:
        if self._on_call is None:
            link = self._link
            if link is not None:
                await link.reply(CallError(call.id, "GenericError", "charger is disconnected"))
            return
        await self._on_call(call)

    async def _drain(self, link: BackendLink) -> None:
        while True:
            if not self._queue:
                if self._on_call is None:
                    return  # no session: park the healthy link, nothing to deliver
                self._queue_changed.clear()
                await self._queue_changed.wait()
                continue
            call = self._queue[0]
            outgoing = call.with_id(new_message_id())
            self._in_flight = call
            try:
                reply = await link.call(outgoing, self._backend.call_timeout)
            except (BackendUnavailable, TimeoutError) as exc:
                logger.warning("%s primary did not confirm queued %s; will retry", self._charger.id, call.action)
                raise BackendUnavailable(f"primary did not confirm queued {call.action}") from exc
            finally:
                self._in_flight = None
            if isinstance(reply, CallError):
                logger.warning(
                    "%s primary rejected queued %s: %s %s",
                    self._charger.id,
                    call.action,
                    reply.code,
                    reply.description,
                )
            self._queue.popleft()
            self._persist_queue()
