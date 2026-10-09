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
from .message_classes import MessageClass, classify, latest_key
from .ocpp import Call, CallError, CallResult, Reply, new_message_id, now_iso, to_dict
from .state import StateStore, restore_outbox
from .traffic_log import TrafficLog

logger = logging.getLogger(__name__)

# Dropped first when the queue overflows: meter data is expendable, stops are not.
EXPENDABLE_ACTION = "MeterValues"

OnCall = Callable[[Call], Awaitable[None]]


class PrimaryUnavailable(Exception):
    """A call that needs the primary's own answer cannot get one: the charger must not get
    a substitute either, but go offline and handle the message by its own OCPP rules."""


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
        self._connected = asyncio.Event()  # set exactly while _link is registered
        self._disconnected = asyncio.Event()  # its inverse
        self._disconnected.set()
        self._reader: asyncio.Task | None = None
        self._boot_resend: asyncio.Task | None = None
        # Set once the cached boot has been replayed on the current link (or needs no replay):
        # nothing else goes out before it, so a backend that wants a boot first gets one.
        self._boot_replayed = asyncio.Event()
        self._boot_replayed.set()
        # (payload, reply) of the replayed boot until the charger's own identical boot
        # consumes it: the primary should not see two boots on one connection.
        self._replayed_boot: tuple[dict, CallResult] | None = None
        # Billing data (stops, transaction meter readings), oldest first.
        self._queue: deque[Call] = deque(call for call, _ in restore_outbox(store.state.primary_outbox))
        # The newest unconfirmed state report per subject; sent once the queue is empty.
        self._latest: dict[str, Call] = {
            latest_key(call): call for call, _ in restore_outbox(list(store.state.primary_latest.values()))
        }
        self._in_flight: Call | None = None  # the queued head whose reply the drain awaits
        self._queue_changed = asyncio.Event()
        # Set while no billing data waits in the queue (a StartTransaction waits for it).
        self._drained = asyncio.Event()
        if not self._queue:
            self._drained.set()
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
        if self._backlog:
            self._ensure_worker()

    @property
    def _backlog(self) -> bool:
        return bool(self._queue or self._latest)

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    async def wait_connected(self, timeout: float) -> bool:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._connected.wait(), timeout)
        return self.connected

    async def wait_disconnected(self) -> None:
        await self._disconnected.wait()

    async def call(self, call: Call, timeout: float) -> Reply:
        """Relay a charger call; raises PrimaryUnavailable for a LIVE call the primary cannot answer."""
        # While the backlog drains, billing data and state reports wait their turn: sent
        # live they would overtake older queued messages and the primary would see a new
        # status before the previous transaction's stop.
        kind = classify(call)
        if kind in (MessageClass.DURABLE, MessageClass.LATEST) and self._backlog:
            return self._unavailable(call)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        # Only a call that needs the primary's own answer waits for a reconnect.
        if self._link is None and (kind is not MessageClass.LIVE or not await self.wait_connected(timeout)):
            return self._unavailable(call)
        if not await self._await_boot_replay(max(deadline - loop.time(), 0)):
            return self._unavailable(call)
        if call.action == "BootNotification":
            consumed = self._consume_replayed_boot(call)
            if consumed is not None:
                return consumed
            self._remember_boot(call)
        if call.action == "StartTransaction" and not await self._wait_drained(timeout):
            # The previous transaction's stop is still queued: the primary must see it
            # first. Failing now makes the charger retry instead of overtaking it.
            logger.warning("%s primary still has queued messages; refusing StartTransaction", self._charger.id)
            return CallError(call.id, "GenericError", "primary backend still catching up")
        link = self._link
        if link is None:
            return self._unavailable(call)
        try:
            return await link.call(call, max(deadline - loop.time(), 0.1))
        except BackendUnavailable:
            return self._unavailable(call)
        except TimeoutError:
            # A wedged link is useless: closing it makes the worker reconnect (UC-003 A1).
            await link.close()
            return self._unavailable(call)

    async def _await_boot_replay(self, timeout: float) -> bool:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._boot_replayed.wait(), timeout)
        return self._boot_replayed.is_set() and self._link is not None

    def _consume_replayed_boot(self, call: Call) -> Reply | None:
        """The charger's boot matches the one just replayed on this connection: answer with
        the primary's earlier reply (fresh clock) instead of booting twice."""
        replayed, self._replayed_boot = self._replayed_boot, None
        if replayed is None or replayed[0] != call.payload:
            return None
        return CallResult(call.id, {**replayed[1].payload, "currentTime": now_iso()})

    async def _wait_drained(self, timeout: float) -> bool:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._drained.wait(), timeout)
        return self._drained.is_set()

    def _remember_boot(self, call: Call) -> None:
        """Cache the charger's boot: the next reconnect replays it to the primary."""
        self._store.state.boot = call.payload
        self._store.save()

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
        self._store.flush()

    def _unavailable(self, call: Call) -> Reply:
        """The primary cannot take this call now: keep what matters, answer the charger."""
        match classify(call):
            case MessageClass.DURABLE:
                self._queue.append(call)
                self._drained.clear()
                self._enforce_queue_limit()
                self._persist()
                logger.warning("%s queued %s while the primary backend is unavailable", self._charger.id, call.action)
                return self._queued_reply(call)
            case MessageClass.LATEST:
                self._latest[latest_key(call)] = call
                self._persist()
                logger.warning("%s kept %s for later: primary unavailable", self._charger.id, call.action)
                return self._queued_reply(call)
            case MessageClass.DROPPABLE:
                if call.action == "Heartbeat":
                    # The charger only asks to sync its clock; answer it locally instead of an
                    # error, so an outage does not make the charger treat it as a protocol fault.
                    logger.warning("%s answered Heartbeat locally: primary unavailable", self._charger.id)
                    return CallResult(call.id, {"currentTime": now_iso()})
                logger.info("%s dropped %s: primary unavailable", self._charger.id, call.action)
                return CallResult(call.id, {})
            case MessageClass.LIVE:
                raise PrimaryUnavailable(f"primary backend cannot answer {call.action}")

    def _queued_reply(self, call: Call) -> Reply:
        self._queue_changed.set()
        self._ensure_worker()
        return CallResult(call.id, {})

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

    def _persist(self) -> None:
        self._store.state.primary_outbox = [to_dict(call) for call in self._queue]
        self._store.state.primary_latest = {key: to_dict(call) for key, call in self._latest.items()}
        self._store.save_soon()

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
            self._connected.set()
            self._disconnected.clear()
            self._resend_boot(link)
            return link

    def _resend_boot(self, link: BackendLink) -> None:
        """Some backends only accept calls from a charger that booted on this very
        connection: replay the cached boot next to the reader, never in the way of
        attach() (a slow boot answer must not fail the charger's connection)."""
        if self._store.state.boot is None:
            self._boot_replayed.set()
            return
        self._boot_replayed = asyncio.Event()
        self._replayed_boot = None
        self._boot_resend = asyncio.create_task(self._send_boot(link), name="primary-boot-replay")

    async def _send_boot(self, link: BackendLink) -> None:
        boot = self._store.state.boot
        try:
            reply = await link.call(Call(new_message_id(), "BootNotification", boot), self._backend.call_timeout)
        except TimeoutError, BackendUnavailable:
            logger.warning("%s did not confirm the replayed BootNotification; continuing anyway", self._charger.id)
            return
        finally:
            self._boot_replayed.set()
        if isinstance(reply, CallResult) and reply.payload.get("status") == "Accepted":
            self._replayed_boot = (boot, reply)
            return
        logger.warning("%s did not accept the replayed BootNotification: %s", self._charger.id, reply)

    async def _discard(self, link: BackendLink | None) -> None:
        """Deregister one link and stop its reader; its in-flight calls have failed (UC-003 A1)."""
        if link is None or self._link is not link:
            return
        self._link = None
        self._connected.clear()
        self._disconnected.set()
        self._replayed_boot = None
        resend = self._boot_resend
        self._boot_resend = None
        if resend is not None:
            resend.cancel()
        self._boot_replayed.set()  # releases anything waiting on a link that is gone
        reader = self._reader
        self._reader = None
        if resend is not None:
            await asyncio.gather(resend, return_exceptions=True)
        if reader is not None:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        # Shielded: cancelling the worker here (server shutdown) must still leave the link closed.
        await asyncio.shield(link.close())

    async def _run(self) -> None:
        backoff = Backoff()
        while not self._closed:
            if self._link is None:
                if self._on_call is None and not self._backlog:
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
            if dead or (self._on_call is None and not self._backlog):
                await self._discard(link)
                if dead and (self._on_call is not None or self._backlog):
                    await self._backoff_sleep(backoff.next_delay())
            # A parked link stays registered and is pumped again on the next loop turn.

    async def _backoff_sleep(self, delay: float) -> None:
        """Wait out a retry delay, but continue at once when a new session attaches."""
        self._wake.clear()  # a wake-up from before this sleep began has been dealt with already
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), delay)

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
        await self._boot_replayed.wait()
        while True:
            if self._queue:
                await self._send_queue_head(link)
            elif self._latest:
                await self._send_latest(link)
            elif self._on_call is None:
                return  # no session: park the healthy link, nothing to deliver
            else:
                self._queue_changed.clear()
                await self._queue_changed.wait()

    async def _send_queue_head(self, link: BackendLink) -> None:
        call = self._queue[0]
        self._in_flight = call
        try:
            reply = await self._deliver(link, call)
        finally:
            self._in_flight = None
        self._log_rejection(call, reply)
        self._queue.popleft()
        if not self._queue:
            self._drained.set()
        self._persist()

    async def _send_latest(self, link: BackendLink) -> None:
        key, call = next(iter(self._latest.items()))
        reply = await self._deliver(link, call)
        self._log_rejection(call, reply)
        if self._latest.get(key) is call:  # a newer report may have replaced it meanwhile
            del self._latest[key]
        self._persist()

    async def _deliver(self, link: BackendLink, call: Call) -> Reply:
        try:
            return await link.call(call.with_id(new_message_id()), self._backend.call_timeout)
        except (BackendUnavailable, TimeoutError) as exc:
            logger.warning("%s primary did not confirm queued %s; will retry", self._charger.id, call.action)
            raise BackendUnavailable(f"primary did not confirm queued {call.action}") from exc

    def _log_rejection(self, call: Call, reply: Reply) -> None:
        if isinstance(reply, CallError):
            logger.warning(
                "%s primary rejected queued %s: %s %s", self._charger.id, call.action, reply.code, reply.description
            )
