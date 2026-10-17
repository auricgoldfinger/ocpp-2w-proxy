"""A single OCPP-J WebSocket connection from the proxy (acting as charger) to one backend."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI
from websockets.protocol import State

from .backend_auth import MissingChargerCredentials
from .durations import format_duration
from .flow import FlowRecorder
from .ocpp import Call, Message, ProtocolError, Reply, parse, protocol_error_reply, serialize, substitute_reply

logger = logging.getLogger(__name__)

SUBPROTOCOL = "ocpp1.6"

# Everything that can stop a backend connection from opening.
CONNECT_ERRORS = (OSError, TimeoutError, InvalidHandshake, InvalidURI, MissingChargerCredentials)

# Why a channel closes a link that is still up (see BackendLink.mark_closing).
IDLE = "idle: no session and nothing queued"
SHUTTING_DOWN = "proxy shutting down"


class BackendUnavailable(Exception):
    """The connection closed (or never opened) before a reply arrived."""


async def send_reply(link: BackendLink | None, message: Reply, backend_name: str) -> None:
    """Deliver a reply if the backend link is alive; log and drop it otherwise (UC-004 A4)."""
    if link is None:
        logger.warning("%s backend offline; reply %s dropped", backend_name, message.id)
        return
    try:
        await link.send(message)
    except BackendUnavailable:
        logger.warning("%s backend went offline; reply %s dropped", backend_name, message.id)


class BackendLink:
    """One connection to a backend. It reports itself up when its channel says it is usable
    (``report_up``) and down, with the reason, when its reader ends."""

    def __init__(self, name: str, ws: ClientConnection, traffic: FlowRecorder):
        self.name = name
        self._ws = ws
        self._traffic = traffic
        self._pending: dict[str, asyncio.Future[Reply]] = {}
        self.connected_since = time.time()
        self._opened_at = time.monotonic()
        self._last_frame_at: float | None = None
        self._closing_cause: str | None = None  # why the proxy itself gave up on this link
        self._served = False  # the reader has ended and reported the link down

    @classmethod
    async def open(
        cls,
        name: str,
        url: str,
        headers: Mapping[str, str],
        user_agent: str | None,
        traffic: FlowRecorder,
    ) -> BackendLink:
        ws = await connect(
            url,
            subprotocols=[SUBPROTOCOL],
            additional_headers=dict(headers),
            user_agent_header=user_agent,
            proxy=None,
            open_timeout=20,
        )
        if ws.subprotocol != SUBPROTOCOL:
            logger.warning("%s backend negotiated subprotocol %r instead of %s", name, ws.subprotocol, SUBPROTOCOL)
        logger.info("connected to %s backend %s", name, url)
        return cls(name, ws, traffic)

    def report_up(self, note: str | None = None) -> None:
        """The channel registered this link and may use it now; ``note`` says how it started."""
        self._traffic.link_changed(self.name, True, note)

    def mark_closing(self, cause: str) -> None:
        """Record why the proxy gives up on this link. Call it before cancelling the reader:
        that reports the link down at once. The first cause wins, and a link whose reader
        already ended keeps the reason it was reported with."""
        if self._closing_cause is None and not self._served:
            self._closing_cause = cause

    @property
    def disconnect_reason(self) -> str:
        """Who closed the link and why, from the proxy's own cause or the close frames."""
        if self._closing_cause is not None:
            return f"closed by proxy: {self._closing_cause}"
        protocol = self._ws.protocol
        received, sent = protocol.close_rcvd, protocol.close_sent
        if received is not None and (sent is None or protocol.close_rcvd_then_sent):
            return f"closed by {self.name}: {received}"
        if sent is not None:
            return f"closed by proxy: {sent}"  # websockets itself, e.g. a keepalive ping timeout
        if protocol.state is State.OPEN:
            return "closed by proxy: reader stopped"  # a caller that gave no cause
        return "dropped without close frame"

    async def send(self, message: Message) -> None:
        self._traffic.sent(self.name, message)
        try:
            await self._ws.send(serialize(message))
        except ConnectionClosed as exc:
            raise BackendUnavailable(f"{self.name} connection closed") from exc

    async def reply(self, message: Reply) -> None:
        await self.send(message)

    async def call(self, call: Call, timeout: float) -> Reply:
        """Send a Call and wait for the matching CallResult / CallError."""
        future: asyncio.Future[Reply] = asyncio.get_running_loop().create_future()
        self._pending[call.id] = future
        try:
            await self.send(call)
            return await asyncio.wait_for(future, timeout)
        finally:
            self._pending.pop(call.id, None)

    async def serve(self, on_call: Callable[[Call], Awaitable[None]]) -> None:
        """Read frames until the connection closes. Calls go to on_call, replies resolve call()."""
        try:
            async for text in self._ws:
                self._last_frame_at = time.monotonic()
                try:
                    message = parse(text)
                except ProtocolError as exc:
                    # Answer the malformed frame with a ProtocolError CallError so the
                    # backend stops waiting for an answer it will never get.
                    logger.warning("%s backend sent a malformed frame (%s)", self.name, exc)
                    if (error := protocol_error_reply(exc)) is not None:
                        await self.send(error)
                    elif (substitute := substitute_reply(exc)) is not None:
                        self._resolve(substitute)  # our Call ends now instead of timing out
                    continue
                self._traffic.received(self.name, message)
                if isinstance(message, Call):
                    await on_call(message)
                else:
                    self._resolve(message)
        except ConnectionClosed:
            pass  # reported below, like every other end of the link
        finally:
            self._served = True
            note = self._down_note()
            logger.warning("%s backend link down: %s", self.name, note)
            self._traffic.link_changed(self.name, False, note)
            self._fail_pending()

    def _down_note(self) -> str:
        """The reason plus uptime and silence, which tell an idle timeout from a session limit."""
        now = time.monotonic()
        parts = [self.disconnect_reason, f"up {format_duration(now - self._opened_at)}"]
        if self._last_frame_at is not None:
            parts.append(f"last frame {format_duration(now - self._last_frame_at)} ago")
        return " · ".join(parts)

    def _resolve(self, reply: Reply) -> None:
        future = self._pending.get(reply.id)
        if future is None or future.done():
            logger.warning("%s backend replied to unknown message id %s; ignored", self.name, reply.id)
            return
        future.set_result(reply)

    def _fail_pending(self) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(BackendUnavailable(f"{self.name} connection closed"))
        self._pending.clear()

    async def close(self, cause: str | None = None) -> None:
        if cause is not None:
            self.mark_closing(cause)
        await self._ws.close()
