"""One connected charger: relays between it, the primary backend and the secondary backend.

Routing rules (OCPP 1.6J):
  charger Call         -> primary (its reply goes back to the charger)
                       -> secondary, if the action is forwarded (its reply is kept by the proxy)
  backend Call         -> policy -> charger with a proxy-unique id, or answered by the proxy
  charger CallResult/  -> the backend that issued the command, with its original id
          CallError
The session lives as long as both the charger and the primary are connected; the
secondary reconnects on its own and never ends the session.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .backend_auth import MissingChargerCredentials, backend_headers, backend_url
from .backend_link import BackendLink, BackendUnavailable
from .charger_auth import ChargerIdentity
from .command_router import CommandRouter, ReplyTarget
from .config import Config
from .ocpp import Call, CallError, CallResult, Message, ProtocolError, Reply, new_message_id, parse, serialize
from .policy import CANNED_ANSWERS, CommandPolicy
from .secondary_channel import SecondaryChannel
from .state import StateStore
from .traffic_log import TrafficLog
from .transactions import TransactionMap

logger = logging.getLogger(__name__)

CLOSE_PRIMARY_UNAVAILABLE = 1011

Translate = Callable[[Call], Call | None]


class ChargerSession:
    def __init__(self, ws: ServerConnection, identity: ChargerIdentity, config: Config):
        self._ws = ws
        self._identity = identity
        self._config = config
        self.charger_id = identity.charger.id
        self._traffic = TrafficLog(self.charger_id, config.proxy.log_payloads)
        self._store = StateStore.for_charger(config.proxy.state_dir, self.charger_id)
        self._transactions = TransactionMap(self._store)
        self._router = CommandRouter()
        self._relays: set[asyncio.Task] = set()
        self._primary: BackendLink | None = None
        self._secondary: SecondaryChannel | None = None

    async def run(self) -> None:
        try:
            self._primary = await self._open_primary()
        except (OSError, TimeoutError, InvalidHandshake, InvalidURI, MissingChargerCredentials) as exc:
            logger.error("%s primary backend unavailable (%s); closing charger connection", self.charger_id, exc)
            await self._ws.close(CLOSE_PRIMARY_UNAVAILABLE, "primary backend unavailable")
            return

        self._secondary = self._build_secondary()
        essential = {
            asyncio.create_task(self._read_charger(), name="charger"),
            asyncio.create_task(self._primary.serve(self._on_primary_call), name="primary"),
        }
        background = {asyncio.create_task(self._secondary.run(), name="secondary")} if self._secondary else set()
        try:
            done, _ = await asyncio.wait(essential, return_when=asyncio.FIRST_COMPLETED)
            logger.info("%s session ending (%s closed)", self.charger_id, ", ".join(t.get_name() for t in done))
        finally:
            tasks = essential | background | self._relays
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self._primary.close()
            await self._ws.close(CLOSE_PRIMARY_UNAVAILABLE, "proxy session ended")

    async def _open_primary(self) -> BackendLink:
        backend = self._config.primary
        backend_id = self._identity.charger.primary_id
        return await BackendLink.open(
            backend.name,
            backend_url(backend, backend_id),
            backend_headers(backend, backend_id, self._identity),
            self._identity.user_agent,
            self._traffic,
        )

    def _build_secondary(self) -> SecondaryChannel | None:
        backend = self._config.secondary
        if backend is None:
            return None
        backend_id = self._identity.charger.secondary_id
        return SecondaryChannel(
            config=backend,
            url=backend_url(backend, backend_id),
            headers=backend_headers(backend, backend_id, self._identity),
            user_agent=self._identity.user_agent,
            store=self._store,
            transactions=self._transactions,
            traffic=self._traffic,
            on_call=self._on_secondary_call,
        )

    # --- charger -> backends --------------------------------------------------------------

    async def _read_charger(self) -> None:
        try:
            async for text in self._ws:
                try:
                    message = parse(text)
                except ProtocolError as exc:
                    logger.warning("%s charger sent a malformed frame (%s); ignored", self.charger_id, exc)
                    continue
                self._traffic.frame("charger ->", message)
                if isinstance(message, Call):
                    self._spawn(self._relay_charger_call(message))
                else:
                    await self._route_charger_reply(message)
        except ConnectionClosed as exc:
            logger.info("%s charger disconnected: %s", self.charger_id, exc)

    def _spawn(self, coroutine) -> None:
        # Relays run concurrently so the charger's replies to backend commands keep flowing
        # while a call waits for the primary's answer.
        task = asyncio.create_task(coroutine)
        self._relays.add(task)
        task.add_done_callback(self._relays.discard)

    async def _relay_charger_call(self, call: Call) -> None:
        # Only correlate starts the secondary will actually see; otherwise nothing ever pairs them.
        tracks_start = call.action == "StartTransaction" and self._secondary and self._secondary.forwards(call.action)
        start_ref = new_message_id() if tracks_start else None
        if self._secondary:
            self._secondary.submit(call, start_ref)
        try:
            reply = await self._primary.call(call, self._config.primary.call_timeout)
        except (TimeoutError, BackendUnavailable) as exc:
            logger.warning("%s primary did not answer %s: %r", self.charger_id, call.action, exc)
            if start_ref:
                self._transactions.primary_start_failed(start_ref)
            return
        if start_ref:
            self._record_primary_start(start_ref, reply)
        await self._send_to_charger(reply)

    def _record_primary_start(self, start_ref: str, reply: Reply) -> None:
        transaction_id = reply.payload.get("transactionId") if isinstance(reply, CallResult) else None
        if isinstance(transaction_id, int):
            self._transactions.primary_started(start_ref, transaction_id)
        else:
            self._transactions.primary_start_failed(start_ref)

    async def _route_charger_reply(self, reply: Reply) -> None:
        routed = self._router.take(reply)
        if routed is None:
            logger.warning("%s charger replied to unknown message id %s; ignored", self.charger_id, reply.id)
            return
        try:
            await routed.target.reply(routed.reply)
        except BackendUnavailable:
            logger.warning(
                "%s %s backend gone; reply to %s dropped", self.charger_id, routed.target.name, routed.action
            )

    # --- backends -> charger --------------------------------------------------------------

    async def _on_primary_call(self, call: Call) -> None:
        await self._handle_backend_call(self._primary, self._config.primary.policy, _unchanged, call)

    async def _on_secondary_call(self, call: Call) -> None:
        await self._handle_backend_call(
            self._secondary, self._config.secondary.policy, self._transactions.rewrite_for_charger, call
        )

    async def _handle_backend_call(
        self, origin: ReplyTarget, policy: CommandPolicy, translate: Translate, call: Call
    ) -> None:
        decision = policy.decide(call)
        if isinstance(decision, Call):
            translated = translate(decision)
            if translated is None:
                logger.warning(
                    "%s %s from %s refers to an unknown transaction", self.charger_id, call.action, origin.name
                )
                decision = _rejection(call)
            else:
                await self._send_to_charger(self._router.outbound(origin, translated))
                return
        logger.info("%s %s from %s answered by proxy (policy)", self.charger_id, call.action, origin.name)
        await origin.reply(decision)

    async def _send_to_charger(self, message: Message) -> None:
        self._traffic.frame("-> charger", message)
        try:
            await self._ws.send(serialize(message))
        except ConnectionClosed:
            logger.warning("%s charger gone; message %s dropped", self.charger_id, message.id)


def _unchanged(call: Call) -> Call:
    return call


def _rejection(call: Call) -> Reply:
    if call.action in CANNED_ANSWERS:
        return CallResult(call.id, dict(CANNED_ANSWERS[call.action]))
    return CallError(call.id, "GenericError", "rejected by proxy")
