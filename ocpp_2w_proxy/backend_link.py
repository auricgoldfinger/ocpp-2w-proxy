"""A single OCPP-J WebSocket connection from the proxy (acting as charger) to one backend."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from .ocpp import Call, Message, ProtocolError, Reply, parse, protocol_error_reply, serialize
from .traffic_log import TrafficLog

logger = logging.getLogger(__name__)

SUBPROTOCOL = "ocpp1.6"


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
    def __init__(self, name: str, ws: ClientConnection, traffic: TrafficLog):
        self.name = name
        self._ws = ws
        self._traffic = traffic
        self._pending: dict[str, asyncio.Future[Reply]] = {}

    @classmethod
    async def open(
        cls,
        name: str,
        url: str,
        headers: Mapping[str, str],
        user_agent: str | None,
        traffic: TrafficLog,
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

    async def send(self, message: Message) -> None:
        self._traffic.frame(f"-> {self.name}", message)
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
                try:
                    message = parse(text)
                except ProtocolError as exc:
                    # Answer the malformed frame with a ProtocolError CallError so the
                    # backend stops waiting for an answer it will never get.
                    logger.warning("%s backend sent a malformed frame (%s)", self.name, exc)
                    await self.send(protocol_error_reply(exc))
                    continue
                self._traffic.frame(f"<- {self.name}", message)
                if isinstance(message, Call):
                    await on_call(message)
                else:
                    self._resolve(message)
        except ConnectionClosed as exc:
            logger.warning("%s backend connection lost: %s", self.name, exc)
        finally:
            self._fail_pending()

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

    async def close(self) -> None:
        await self._ws.close()
