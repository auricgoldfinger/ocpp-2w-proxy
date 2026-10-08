"""Resilient, store-and-forward connection to the secondary (billing) backend.

The charger must never notice the secondary backend: it can be slow, down or rejecting,
and charging (driven by the primary) carries on. Transaction-related calls are queued on
disk and replayed in order once the secondary is reachable again.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from websockets.exceptions import InvalidHandshake, InvalidURI

from .backend_link import BackendLink, BackendUnavailable, send_reply
from .backoff import Backoff
from .config import SecondaryConfig
from .ocpp import Call, CallError, CallResult, Reply, from_dict, new_message_id, to_dict
from .policy import CommandPolicy
from .state import StateStore
from .traffic_log import TrafficLog
from .transactions import TransactionMap

logger = logging.getLogger(__name__)

# Calls that matter for billing survive outages and restarts.
DURABLE_ACTIONS = frozenset({"StartTransaction", "StopTransaction", "MeterValues"})
# Dropped first when the queue overflows.
EXPENDABLE_ACTION = "MeterValues"

MAX_ATTEMPTS_PER_CALL = 3
DEFAULT_BOOT_RETRY_INTERVAL = 60


@dataclass
class _QueuedCall:
    call: Call
    durable: bool
    # Correlates a StartTransaction with the primary's answer for the same charger call.
    start_ref: str | None = None
    attempts: int = 0


class SecondaryChannel:
    def __init__(
        self,
        config: SecondaryConfig,
        url: str,
        headers: Mapping[str, str],
        user_agent: str | None,
        store: StateStore,
        transactions: TransactionMap,
        traffic: TrafficLog,
        on_call: Callable[[Call], Awaitable[None]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.name = config.name
        self._config = config
        self._url = url
        self._headers = headers
        self._user_agent = user_agent
        self._store = store
        self._state = store.state
        self._transactions = transactions
        self._traffic = traffic
        self._on_call = on_call
        self._sleep = sleep
        self._link: BackendLink | None = None
        self._queue: deque[_QueuedCall] = deque(
            _QueuedCall(from_dict(item["call"]), durable=True, start_ref=item.get("start_ref"))
            for item in self._state.outboxes.get(config.name, [])
        )
        self._queue_changed = asyncio.Event()
        if self._queue:
            logger.info("%s: %d queued call(s) restored from disk", self.name, len(self._queue))

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
        self._queue_changed.set()

    def _remember(self, call: Call) -> None:
        if call.action == "BootNotification":
            self._state.boot = call.payload
            self._store.save()
        elif call.action == "StatusNotification":
            self._state.statuses[str(call.payload.get("connectorId"))] = call.payload
            self._store.save()

    def _enforce_queue_limit(self) -> None:
        while sum(item.durable for item in self._queue) > self._config.max_queue:
            victim = next((i for i in self._queue if i.call.action == EXPENDABLE_ACTION), self._queue[0])
            self._queue.remove(victim)
            logger.error("%s queue full (%d); dropped queued %s", self.name, self._config.max_queue, victim.call.action)

    def _persist_queue(self) -> None:
        self._state.outboxes[self._config.name] = [
            {"call": to_dict(item.call), "start_ref": item.start_ref} for item in self._queue if item.durable
        ]
        self._store.save()

    # --- secondary -> charger replies -----------------------------------------------------

    async def reply(self, message: Reply) -> None:
        await send_reply(self._link, message, self.name)

    # --- connection lifecycle -------------------------------------------------------------

    async def run(self) -> None:
        backoff = Backoff()
        while True:
            try:
                link = await BackendLink.open(self.name, self._url, self._headers, self._user_agent, self._traffic)
            except (OSError, TimeoutError, InvalidHandshake, InvalidURI) as exc:
                logger.warning("%s unreachable (%s); retrying in ~%.0fs", self.name, exc, backoff.current)
            else:
                try:
                    if await self._serve(link):
                        backoff.reset()
                except Exception:
                    # Never let a bug end the secondary for the rest of the charger session.
                    logger.exception("%s connection failed unexpectedly", self.name)
            await self._sleep(backoff.next_delay())

    async def _serve(self, link: BackendLink) -> bool:
        """Run one connection until it drops. Returns True if the boot was accepted."""
        self._link = link
        booted = asyncio.Event()
        reader = asyncio.create_task(link.serve(self._on_call))
        sender = asyncio.create_task(self._sync_and_drain(link, booted))
        try:
            done, _ = await asyncio.wait({reader, sender}, return_when=asyncio.FIRST_COMPLETED)
            if sender in done and not sender.cancelled() and sender.exception():
                exc = sender.exception()
                if not isinstance(exc, BackendUnavailable):
                    logger.error("%s sender failed: %r", self.name, exc)
        finally:
            self._link = None
            for task in (reader, sender):
                task.cancel()
            await asyncio.gather(reader, sender, return_exceptions=True)
            await link.close()
            self._drop_transient()
        return booted.is_set()

    def _drop_transient(self) -> None:
        self._queue = deque(item for item in self._queue if item.durable)

    async def _sync_and_drain(self, link: BackendLink, booted: asyncio.Event) -> None:
        await self._boot(link)
        booted.set()
        await self._send_statuses(link)
        while True:
            while not self._queue:
                self._queue_changed.clear()
                await self._queue_changed.wait()
            await self._send_head(link)

    async def _boot(self, link: BackendLink) -> None:
        if not self.forwards("BootNotification"):
            return  # this backend is not configured to receive boots
        if self._queue and self._queue[0].call.action == "BootNotification":
            return  # the charger just booted; its own BootNotification is first in line
        if self._state.boot is None:
            logger.warning("no BootNotification cached yet; %s gets none until the charger reboots", self.name)
            return
        while True:
            try:
                reply = await link.call(Call(new_message_id(), "BootNotification", self._state.boot), self._timeout)
            except TimeoutError:
                await self._sleep(DEFAULT_BOOT_RETRY_INTERVAL)
                continue
            if isinstance(reply, CallResult) and reply.payload.get("status") == "Accepted":
                return
            interval = DEFAULT_BOOT_RETRY_INTERVAL
            if isinstance(reply, CallResult):
                interval = max(10, int(reply.payload.get("interval") or DEFAULT_BOOT_RETRY_INTERVAL))
            logger.warning("%s did not accept BootNotification (%s); retry in %ss", self.name, reply, interval)
            await self._sleep(interval)

    async def _send_statuses(self, link: BackendLink) -> None:
        if not self.forwards("StatusNotification"):
            return
        for payload in list(self._state.statuses.values()):
            try:
                await link.call(Call(new_message_id(), "StatusNotification", payload), self._timeout)
            except TimeoutError:
                logger.warning("%s did not answer StatusNotification", self.name)

    async def _send_head(self, link: BackendLink) -> None:
        item = self._queue[0]
        outgoing = self._transactions.rewrite_for_secondary(item.call, self.name)
        if outgoing is None:
            self._complete(item)
            return
        try:
            reply = await link.call(outgoing, self._timeout)
        except TimeoutError:
            item.attempts += 1
            if item.attempts < MAX_ATTEMPTS_PER_CALL:
                logger.warning("%s did not answer %s (attempt %d)", self.name, item.call.action, item.attempts)
                return
            logger.error("%s never answered %s; dropped", self.name, item.call.action)
            self._complete(item)
            return
        self._complete(item)
        self._handle_result(item, reply)

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
            return
        handler = _RESULT_HANDLERS.get(item.call.action)
        if handler:
            handler(self, item, reply)

    def _start_transaction_result(self, item: _QueuedCall, reply: CallResult) -> None:
        _warn_if_not_accepted(reply, "StartTransaction")
        transaction_id = reply.payload.get("transactionId")
        if isinstance(transaction_id, int) and item.start_ref:
            self._transactions.secondary_started(self.name, item.start_ref, transaction_id)

    def _stop_transaction_result(self, item: _QueuedCall, reply: CallResult) -> None:
        primary_tx = item.call.payload.get("transactionId")
        if isinstance(primary_tx, int):
            self._transactions.forget(primary_tx)

    def _authorize_result(self, item: _QueuedCall, reply: CallResult) -> None:
        _warn_if_not_accepted(reply, "Authorize")

    def _boot_notification_result(self, item: _QueuedCall, reply: CallResult) -> None:
        if reply.payload.get("status") != "Accepted":
            logger.warning(
                "secondary backend answered the charger's BootNotification with %r", reply.payload.get("status")
            )


def _warn_if_not_accepted(reply: CallResult, action: str) -> None:
    status = reply.payload.get("idTagInfo", {}).get("status")
    if status != "Accepted":
        logger.warning(
            "secondary backend answered %s with idTag status %r: this session may not be billed", action, status
        )


_RESULT_HANDLERS: Mapping[str, Callable[[SecondaryChannel, _QueuedCall, CallResult], None]] = {
    "StartTransaction": SecondaryChannel._start_transaction_result,
    "StopTransaction": SecondaryChannel._stop_transaction_result,
    "Authorize": SecondaryChannel._authorize_result,
    "BootNotification": SecondaryChannel._boot_notification_result,
}
