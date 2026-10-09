"""Resilient, store-and-forward connection to the secondary (billing) backend.

The charger must never notice the secondary backend: it can be slow, down or rejecting,
and charging (driven by the primary) carries on. Transaction-related calls are queued on
disk and replayed in order once the secondary is reachable again.

The channel belongs to the server, not to a charger session: it connects while a session
is attached or billing data waits, replays the cached boot/status on every (re)connection,
and drains its queue even after the charger has gone. Once the queue is empty and no
session is attached it disconnects, so the backend does not see an offline charger as
online.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from .backend_auth import backend_headers, backend_url
from .backend_link import CONNECT_ERRORS, BackendLink, BackendUnavailable, send_reply
from .backoff import Backoff
from .charger_auth import ChargerIdentity
from .config import ChargerConfig, SecondaryConfig
from .ocpp import Call, CallError, CallResult, Reply, is_accepted, new_message_id, to_dict
from .policy import CommandPolicy
from .state import StateStore, restore_outbox
from .traffic_log import TrafficLog
from .transactions import TransactionMap

logger = logging.getLogger(__name__)

# Calls that matter for billing survive outages and restarts.
DURABLE_ACTIONS = frozenset({"StartTransaction", "StopTransaction", "MeterValues"})
# Dropped first when the queue overflows.
EXPENDABLE_ACTION = "MeterValues"

DEFAULT_BOOT_RETRY_INTERVAL = 60
MIN_BOOT_RETRY_INTERVAL = 10
# UC-006 A5: a billing message the backend answers with a CallError is retried (OCPP 1.6
# TransactionMessageAttempts); after this many CallErrors in a row it is dropped, so one
# message the backend can never accept does not hold up every later one.
MAX_CALL_ERROR_ATTEMPTS = 5
# Transient calls (heartbeats, authorizations, ...) wait only while connected, but a backend
# that keeps refusing the boot would let them pile up: the oldest are dropped past this.
MAX_TRANSIENT_QUEUE = 1000

OnCall = Callable[[Call], Awaitable[None]]


@dataclass
class _QueuedCall:
    call: Call
    durable: bool
    # Correlates a StartTransaction with the primary's answer for the same charger call.
    start_ref: str | None = None
    # CallErrors in a row for this message; kept in memory only, so a restart starts over.
    call_errors: int = 0


class SecondaryChannel:
    def __init__(
        self,
        config: SecondaryConfig,
        charger: ChargerConfig,
        store: StateStore,
        transactions: TransactionMap,
        traffic: TrafficLog,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.name = config.name
        self._config = config
        self._charger = charger
        backend_id = charger.secondary_ids.get(config.name, charger.id)
        self._url = backend_url(config, backend_id)
        # auth = 'forward' is refused for secondaries, so the headers never depend on
        # the charger's session credentials: they are built once, here.
        self._headers = backend_headers(config, backend_id, ChargerIdentity(charger, None, None))
        self._store = store
        self._state = store.state
        self._transactions = transactions
        self._traffic = traffic
        self._sleep = sleep
        # A session is attached exactly while _on_call is set; the generation guards
        # that routing so a replaced session cannot unhook its successor (UC-002 BR-004).
        self._on_call: OnCall | None = None
        self._generation = 0
        self._user_agent: str | None = None
        self._link: BackendLink | None = None
        self._in_flight: _QueuedCall | None = None  # the queued head whose reply the sender awaits
        self._queue: deque[_QueuedCall] = deque(
            _QueuedCall(call, durable=True, start_ref=start_ref)
            for call, start_ref in restore_outbox(self._state.outboxes.get(config.name, []))
        )
        self._queue_changed = asyncio.Event()
        self._worker: asyncio.Task | None = None
        self._heartbeater: asyncio.Task | None = None
        self._heartbeat_interval: int | None = None
        # UC-006 BR-004: message retries back off on their own schedule, reset only by a
        # delivered message, so a message that keeps failing is not retried every few seconds.
        self._retry_backoff = Backoff()
        self._delivery_failed = False
        self._closed = False
        if self._queue:
            logger.info("%s: %d queued call(s) restored from disk", self.name, len(self._queue))

    # --- charger session wiring ----------------------------------------------------------

    def attach(self, identity: ChargerIdentity, on_call: OnCall) -> int:
        """Route this backend's commands into the charger session until detach().

        The returned token ties the routing to this attach: hand it to detach(). The
        connection itself is owned by the background worker and never waits for a session.
        """
        self._user_agent = identity.user_agent
        self._on_call = on_call
        self._generation += 1
        self._queue_changed.set()  # wakes an idle worker: it connects now
        return self._generation

    def detach(self, token: int) -> None:
        """End charger-facing command routing; the durable outbox keeps draining."""
        if token != self._generation:
            return  # a newer session owns the channel now
        self._on_call = None
        self._queue_changed.set()  # wakes the sender: with nothing left to deliver it disconnects

    def start_background(self) -> None:
        """Start the worker at server startup; it connects once there is something to do."""
        self._ensure_worker()

    def _wanted(self) -> bool:
        """A connection is needed: a session is attached or billing data is waiting."""
        return self._on_call is not None or any(item.durable for item in self._queue)

    async def close(self) -> None:
        self._closed = True
        worker = self._worker
        if worker is not None:
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
        self._store.flush()

    def _ensure_worker(self) -> None:
        if not self._closed and (self._worker is None or self._worker.done()):
            self._worker = asyncio.create_task(self._run(), name=f"secondary-{self.name}-{self._charger.id}")

    @property
    def connected(self) -> bool:
        return self._link is not None

    @property
    def policy(self) -> CommandPolicy:
        return self._config.policy

    # --- charger -> secondary -------------------------------------------------------------

    def forwards(self, action: str) -> bool:
        return action in self._config.forward_actions

    def submit(self, call: Call, start_ref: str | None = None) -> None:
        if not self.forwards(call.action):
            return
        self._remember(call)
        durable = call.action in DURABLE_ACTIONS
        if not durable and not self.connected:
            return  # stale when replayed later; boot/status are re-sent from the cache instead
        # Own message id: the charger's ids restart after a reboot and must not collide in the queue.
        self._queue.append(_QueuedCall(call.with_id(new_message_id()), durable, start_ref))
        if durable:
            self._enforce_queue_limit()
            self._persist_queue()
        else:
            self._enforce_transient_limit()
        self._queue_changed.set()

    def _remember(self, call: Call) -> None:
        if call.action == "BootNotification":
            self._state.boot = call.payload
            self._store.save_soon()
        elif call.action == "StatusNotification":
            self._state.statuses[str(call.payload.get("connectorId"))] = call.payload
            self._store.save_soon()

    def _enforce_queue_limit(self) -> None:
        while sum(item.durable for item in self._queue) > self._config.max_queue:
            victim = self._sacrifice()
            if victim is None:
                break  # only the in-flight head is over the limit; its confirmation pops it
            self._queue.remove(victim)
            logger.error("%s queue full (%d); dropped queued %s", self.name, self._config.max_queue, victim.call.action)

    def _enforce_transient_limit(self) -> None:
        while sum(not item.durable for item in self._queue) > MAX_TRANSIENT_QUEUE:
            victim = next(i for i in self._queue if not i.durable and i is not self._in_flight)
            self._queue.remove(victim)
            logger.warning("%s dropped stale %s: too many waiting calls", self.name, victim.call.action)

    def _sacrifice(self) -> _QueuedCall | None:
        """The first droppable durable item: meter data first, oldest otherwise. Never the
        head currently being sent - removing it would make the sender pop the next
        item, which was never sent, losing it silently. Transient items do not count
        toward the limit, so dropping one would not relieve it."""
        candidates = [i for i in self._queue if i.durable and i is not self._in_flight]
        if not candidates:
            return None
        return next((i for i in candidates if i.call.action == EXPENDABLE_ACTION), candidates[0])

    def _persist_queue(self) -> None:
        self._state.outboxes[self._config.name] = [
            {"call": to_dict(item.call), "start_ref": item.start_ref} for item in self._queue if item.durable
        ]
        self._store.save_soon()

    # --- secondary -> charger replies -----------------------------------------------------

    async def reply(self, message: Reply) -> None:
        await send_reply(self._link, message, self.name)

    # --- connection lifecycle -------------------------------------------------------------

    async def _run(self) -> None:
        """Connect, serve, reconnect: for as long as the server runs."""
        backoff = Backoff()
        while not self._closed:
            if not self._wanted():
                self._queue_changed.clear()
                await self._queue_changed.wait()
                continue
            try:
                link = await BackendLink.open(self.name, self._url, self._headers, self._user_agent, self._traffic)
            except CONNECT_ERRORS as exc:
                logger.warning("%s unreachable (%s); retrying in ~%.0fs", self.name, exc, backoff.current)
            else:
                try:
                    if await self._serve(link):
                        backoff.reset()
                except Exception:
                    # Never let a bug end the secondary for the rest of the server run.
                    logger.exception("%s connection failed unexpectedly", self.name)
            if self._wanted():
                await self._sleep(self._next_delay(backoff))

    def _next_delay(self, backoff: Backoff) -> float:
        """The reconnect delay, but at least the message-retry delay when the connection
        ended because a queued message was not answered."""
        delay = backoff.next_delay()
        if self._delivery_failed:
            self._delivery_failed = False
            delay = max(delay, self._retry_backoff.next_delay())
        return delay

    async def _serve(self, link: BackendLink) -> bool:
        """Run one connection until it drops. Returns True if the boot was accepted."""
        self._link = link
        booted = asyncio.Event()
        reader = asyncio.create_task(link.serve(self._dispatch_call))
        sender = asyncio.create_task(self._sync_and_drain(link, booted))
        try:
            done, _ = await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
            if sender in done and not sender.cancelled() and sender.exception():
                exc = sender.exception()
                if not isinstance(exc, BackendUnavailable):
                    logger.error("%s sender failed: %r", self.name, exc)
        finally:
            self._link = None
            heartbeat = self._heartbeater
            self._heartbeater = None
            tasks = [reader, sender] + ([heartbeat] if heartbeat is not None else [])
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            # Shielded: closing the worker (server shutdown) must still close the link.
            await asyncio.shield(link.close())
            self._drop_transient()
        return booted.is_set()

    async def _dispatch_call(self, call: Call) -> None:
        if self._on_call is None:
            link = self._link
            if link is not None:
                await link.reply(CallError(call.id, "GenericError", "charger is disconnected"))
            return
        await self._on_call(call)

    def _drop_transient(self) -> None:
        self._queue = deque(item for item in self._queue if item.durable)

    async def _sync_and_drain(self, link: BackendLink, booted: asyncio.Event) -> None:
        if not await self._boot(link):
            return  # nobody needs this connection any more
        booted.set()
        self._start_heartbeats(link)
        await self._send_statuses(link)
        while True:
            while not self._queue:
                if not self._wanted():
                    return  # drained and no session: disconnect
                self._queue_changed.clear()
                await self._queue_changed.wait()
            await self._send_head(link)

    def _start_heartbeats(self, link: BackendLink) -> None:
        """Keep the backend's idle timeout alive with synthetic heartbeats when the
        charger's own ones are not forwarded to it; it stated its interval at boot."""
        if self._heartbeat_interval is None or self._heartbeater is not None:
            return
        self._heartbeater = asyncio.create_task(self._heartbeat_loop(link), name=f"secondary-heartbeat-{self.name}")

    async def _heartbeat_loop(self, link: BackendLink) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_interval or DEFAULT_BOOT_RETRY_INTERVAL)
            try:
                await link.call(Call(new_message_id(), "Heartbeat", {}), self._timeout)
            except TimeoutError:
                continue  # no answer: try again at the next interval
            except BackendUnavailable:
                return  # the link is gone; _serve tears everything down

    def _note_heartbeat_interval(self, reply: CallResult) -> None:
        """Remember the interval this backend asked for in its BootNotification.conf."""
        if self.forwards("Heartbeat"):
            return  # the charger's own heartbeats reach this backend already
        interval = reply.payload.get("interval")
        if isinstance(interval, int) and interval > 0:
            self._heartbeat_interval = interval
            self._start_heartbeats(self._link)  # the charger's boot may teach us the interval mid-connection

    async def _boot(self, link: BackendLink) -> bool:
        """Get the backend to accept the boot. False: it never did and nobody needs it now."""
        if not self.forwards("BootNotification"):
            return True  # this backend is not configured to receive boots
        if self._queue and self._queue[0].call.action == "BootNotification":
            # The charger just booted: its own boot is the cached one sent below.
            self._complete(self._queue[0])
        if self._state.boot is None:
            logger.warning("no BootNotification cached yet; %s gets none until the charger reboots", self.name)
            return True
        return await self._accept_boot(link, self._state.boot)

    async def _accept_boot(self, link: BackendLink, payload: dict) -> bool:
        """Send a boot until the backend accepts it (UC-006 A2), at the interval it names.
        False: it never did and nobody needs the connection any more."""
        while self._wanted():
            try:
                reply = await link.call(Call(new_message_id(), "BootNotification", payload), self._timeout)
            except TimeoutError:
                await self._sleep(DEFAULT_BOOT_RETRY_INTERVAL)
                continue
            if is_accepted(reply):
                self._note_heartbeat_interval(reply)
                return True
            interval = DEFAULT_BOOT_RETRY_INTERVAL
            if isinstance(reply, CallResult):
                interval = max(MIN_BOOT_RETRY_INTERVAL, int(reply.payload.get("interval") or interval))
            logger.warning("%s did not accept BootNotification (%s); retry in %ss", self.name, reply, interval)
            await self._sleep(interval)
        return False

    async def _send_statuses(self, link: BackendLink) -> None:
        if not self.forwards("StatusNotification"):
            return
        for payload in list(self._state.statuses.values()):
            try:
                reply = await link.call(Call(new_message_id(), "StatusNotification", payload), self._timeout)
            except TimeoutError:
                logger.warning("%s did not answer StatusNotification", self.name)
                continue
            if isinstance(reply, CallError):
                logger.warning("%s rejected StatusNotification: %s %s", self.name, reply.code, reply.description)

    async def _send_head(self, link: BackendLink) -> None:
        item = self._queue[0]
        if not self.forwards(item.call.action):
            # Queued before the configuration stopped forwarding it to this backend (UC-006 A9).
            logger.warning(
                "%s no longer forwards %s; queued message discarded (forwarding policy)", self.name, item.call.action
            )
            self._complete(item)
            return
        if item.call.action == "BootNotification":
            # The charger rebooted while connected: the backend must accept it before anything else.
            self._complete(item)
            await self._accept_boot(link, item.call.payload)
            return
        outgoing = self._transactions.rewrite_for_secondary(item.call, self.name)
        if outgoing is None:
            self._complete(item)
            return
        self._in_flight = item
        try:
            reply = await link.call(outgoing, self._timeout)
        except TimeoutError as exc:
            # A silent link is wedged: reconnect and send the message again. A timeout
            # never ends a message's life.
            logger.warning("%s did not answer %s; reconnecting to send it again", self.name, item.call.action)
            self._delivery_failed = True
            raise BackendUnavailable(f"{self.name} did not answer {item.call.action}") from exc
        finally:
            self._in_flight = None
        if isinstance(reply, CallError) and item.durable and await self._retry_after_call_error(item, reply):
            return
        if isinstance(reply, CallResult):
            self._retry_backoff.reset()
        self._complete(item)
        self._handle_result(item, reply)

    async def _retry_after_call_error(self, item: _QueuedCall, reply: CallError) -> bool:
        """Keep a rejected billing message for another attempt (UC-006 A5). False once it
        has used up its attempts: the caller then drops it."""
        item.call_errors += 1
        if item.call_errors >= MAX_CALL_ERROR_ATTEMPTS:
            logger.error(
                "%s rejected %s %d times (%s %s); dropped",
                self.name,
                item.call.action,
                item.call_errors,
                reply.code,
                reply.description,
            )
            return False
        delay = self._retry_backoff.next_delay()
        logger.warning(
            "%s rejected %s (%s %s); retry %d of %d in ~%.0fs",
            self.name,
            item.call.action,
            reply.code,
            reply.description,
            item.call_errors,
            MAX_CALL_ERROR_ATTEMPTS - 1,
            delay,
        )
        await self._sleep(delay)
        return True

    def _forget_stopped_transaction(self, item: _QueuedCall) -> None:
        """This backend will never confirm the stop: end its transaction link so
        later messages are not translated against a transaction it gave up on."""
        primary_tx = item.call.payload.get("transactionId")
        if item.call.action == "StopTransaction" and isinstance(primary_tx, int):
            self._transactions.forget(primary_tx, self.name)

    def _complete(self, item: _QueuedCall) -> None:
        if self._queue and self._queue[0] is item:
            self._queue.popleft()
        if item.durable:
            self._persist_queue()

    @property
    def _timeout(self) -> float:
        return self._config.call_timeout

    # --- results --------------------------------------------------------------------------

    def _handle_result(self, item: _QueuedCall, reply: Reply) -> None:
        if isinstance(reply, CallError):
            logger.warning("%s rejected %s: %s %s", self.name, item.call.action, reply.code, reply.description)
            self._forget_stopped_transaction(item)
            return
        handler = _RESULT_HANDLERS.get(item.call.action)
        if handler:
            handler(self, item, reply)

    def _start_transaction_result(self, item: _QueuedCall, reply: CallResult) -> None:
        self._warn_if_not_accepted(reply, "StartTransaction")
        transaction_id = reply.payload.get("transactionId")
        if isinstance(transaction_id, int) and item.start_ref:
            self._transactions.secondary_started(self.name, item.start_ref, transaction_id)

    def _stop_transaction_result(self, item: _QueuedCall, reply: CallResult) -> None:
        primary_tx = item.call.payload.get("transactionId")
        if isinstance(primary_tx, int):
            # This backend's session ended; other backends keep their links.
            self._transactions.forget(primary_tx, self.name)

    def _authorize_result(self, item: _QueuedCall, reply: CallResult) -> None:
        self._warn_if_not_accepted(reply, "Authorize")

    def _warn_if_not_accepted(self, reply: CallResult, action: str) -> None:
        status = reply.payload.get("idTagInfo", {}).get("status")
        if status != "Accepted":
            logger.warning(
                "secondary backend %s answered %s with idTag status %r: this session may not be billed there",
                self.name,
                action,
                status,
            )


_RESULT_HANDLERS: Mapping[str, Callable[[SecondaryChannel, _QueuedCall, CallResult], None]] = {
    "StartTransaction": SecondaryChannel._start_transaction_result,
    "StopTransaction": SecondaryChannel._stop_transaction_result,
    "Authorize": SecondaryChannel._authorize_result,
}
