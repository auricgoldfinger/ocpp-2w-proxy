"""One connected charger: relays between it, the primary backend and the secondary backends.

Routing rules (OCPP 1.6J):
  charger Call         -> primary (its reply goes back to the charger)
                        -> every secondary whose forward_actions include it
                           (their replies are kept by the proxy)
  backend Call         -> policy -> charger with a proxy-unique id, or answered by the proxy
  charger CallResult/  -> the backend that issued the command, with its original id
           CallError
The session lives as long as the charger is connected. Secondary backends reconnect
independently. The charger mirrors the primary's availability: a call that needs the
primary's own answer and cannot get one closes the charger connection, so the charger
goes offline and keeps and resends the message by its own OCPP offline rules.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from functools import partial

from websockets.asyncio.server import ServerConnection
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .backend_auth import MissingChargerCredentials
from .backend_link import BackendUnavailable
from .charger_auth import ChargerIdentity
from .charger_context import ChargerContext
from .command_router import CommandRouter, ReplyTarget
from .config import Config
from .ocpp import (
    Call,
    CallError,
    CallResult,
    Message,
    ProtocolError,
    Reply,
    new_message_id,
    parse,
    protocol_error_reply,
    serialize,
    substitute_reply,
)
from .policy import CANNED_ANSWERS, CommandPolicy
from .primary_channel import PrimaryUnavailable
from .secondary_channel import SecondaryChannel

logger = logging.getLogger(__name__)

CLOSE_PRIMARY_UNAVAILABLE = 1011
# A normal session end (charger gone, replaced, or proxy shutting down): not an error.
SESSION_ENDED = 1001

Translate = Callable[[Call], Call | None]


class ChargerSession:
    def __init__(
        self,
        ws: ServerConnection,
        identity: ChargerIdentity,
        config: Config,
        charger: ChargerContext,
    ):
        self._ws = ws
        self._identity = identity
        self._config = config
        self._primary = charger.primary
        self.charger_id = identity.charger.id
        self._traffic = charger.traffic
        self._store = charger.store
        self._transactions = charger.transactions
        self._secondaries = dict(charger.secondaries)
        self._router = CommandRouter()
        self._relays: set[asyncio.Task] = set()

    async def run(self) -> None:
        try:
            token = await self._primary.attach(self._identity, self._on_primary_call)
        except (OSError, TimeoutError, InvalidHandshake, InvalidURI, MissingChargerCredentials) as exc:
            logger.error("%s primary backend unavailable (%s); closing charger connection", self.charger_id, exc)
            await self._close_primary_unavailable()
            return

        # The secondary channels are owned by the charger context; this session only
        # hooks its command routing into them.
        secondary_tokens = {
            name: channel.attach(self._identity, partial(self._on_secondary_call, channel))
            for name, channel in self._secondaries.items()
        }
        essential = {asyncio.create_task(self._read_charger(), name="charger")}
        try:
            await asyncio.gather(*essential)
            logger.info("%s charger disconnected; ending session", self.charger_id)
        finally:
            tasks = essential | self._relays
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for name, secondary_token in secondary_tokens.items():
                self._secondaries[name].detach(secondary_token)
            self._primary.detach(token)
            await self._ws.close(SESSION_ENDED, "proxy session ended")

    # --- charger -> backends --------------------------------------------------------------

    async def _read_charger(self) -> None:
        try:
            async for text in self._ws:
                try:
                    message = parse(text)
                except ProtocolError as exc:
                    # Answer the malformed frame with a ProtocolError CallError so the
                    # charger stops waiting for an answer it will never get.
                    logger.warning("%s charger sent a malformed frame (%s)", self.charger_id, exc)
                    if (error := protocol_error_reply(exc)) is not None:
                        await self._send_to_charger(error)
                    elif (substitute := substitute_reply(exc)) is not None:
                        # The backend that issued the command must not wait for it forever.
                        await self._route_charger_reply(substitute)
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
        task.add_done_callback(self._relay_finished)

    def _relay_finished(self, task: asyncio.Task) -> None:
        self._relays.discard(task)
        if not task.cancelled() and task.exception() is not None:
            logger.error("%s relaying a charger call failed", self.charger_id, exc_info=task.exception())

    async def _relay_charger_call(self, call: Call) -> None:
        try:
            if call.action == "StartTransaction":
                await self._relay_start_transaction(call)
                return
            for channel in self._secondaries.values():
                channel.submit(call)
            reply = await self._primary.call(call, self._config.primary.call_timeout)
            await self._send_to_charger(reply)
        except PrimaryUnavailable as exc:
            # Unanswered, the charger keeps the message and resends it once back online
            # (OCPP 1.6 section 3.7); a substitute answer would make it act on a guess.
            logger.warning("%s %s; closing charger connection", self.charger_id, exc)
            await self._close_primary_unavailable()

    async def _close_primary_unavailable(self) -> None:
        await self._ws.close(CLOSE_PRIMARY_UNAVAILABLE, "primary backend unavailable")

    async def _relay_start_transaction(self, call: Call) -> None:
        """A start goes to the primary first and only then to the secondaries.

        The secondaries may only open their sessions for a transaction the primary
        confirmed: its transactionId is the one the charger will use. A start the
        primary refused (or answered while unavailable) would otherwise leave a
        phantom session in every secondary, and the charger's retry would open yet
        another one per attempt (UC-003 step 3).
        """
        reply = await self._primary.call(call, self._config.primary.call_timeout)
        transaction_id = reply.payload.get("transactionId") if isinstance(reply, CallResult) else None
        # Not the charger's message id: chargers restart their ids after a reboot, and the
        # records kept under this reference outlive the session.
        start_ref = new_message_id()
        if isinstance(transaction_id, int):
            # Record the primary's answer first: each secondary's own answer links
            # through it, however long it takes to arrive.
            self._transactions.primary_started(start_ref, transaction_id)
            for channel in self._secondaries.values():
                channel.submit(call, start_ref=start_ref)
        else:
            self._transactions.primary_start_failed(start_ref)
        await self._send_to_charger(reply)

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

    async def _on_secondary_call(self, channel: SecondaryChannel, call: Call) -> None:
        translate = partial(self._transactions.rewrite_for_charger, backend_name=channel.name)
        await self._handle_backend_call(channel, channel.policy, translate, call)

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
                outbound = self._router.outbound(origin, translated)
                if await self._send_to_charger(outbound):
                    return
                # The charger's socket closed under us: tell the backend now rather than
                # leaving it to wait out its own timeout.
                self._router.discard(outbound.id)
                decision = CallError(call.id, "GenericError", "charger is disconnected")
        logger.info("%s %s from %s answered by proxy (policy)", self.charger_id, call.action, origin.name)
        await origin.reply(decision)

    async def _send_to_charger(self, message: Message) -> bool:
        """True if the message was handed to the charger's socket."""
        self._traffic.frame("-> charger", message)
        try:
            await self._ws.send(serialize(message))
        except ConnectionClosed:
            logger.warning("%s charger gone; message %s dropped", self.charger_id, message.id)
            return False
        return True


def _unchanged(call: Call) -> Call:
    return call


def _rejection(call: Call) -> Reply:
    if call.action in CANNED_ANSWERS:
        return CallResult(call.id, dict(CANNED_ANSWERS[call.action]))
    return CallError(call.id, "GenericError", "rejected by proxy")
