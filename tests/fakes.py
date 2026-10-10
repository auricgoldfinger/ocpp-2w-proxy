"""Test doubles: a scriptable fake CSMS backend and a fake charger client."""

from __future__ import annotations

import asyncio
import base64
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.asyncio.server import Server, ServerConnection, serve

Responder = Callable[[str, dict[str, Any]], dict[str, Any] | None]


def default_responder(action: str, payload: dict[str, Any]) -> dict[str, Any] | None:
    return {
        "BootNotification": {"status": "Accepted", "currentTime": "2026-01-01T00:00:00Z", "interval": 60},
        "Heartbeat": {"currentTime": "2026-01-01T00:00:00Z"},
        "Authorize": {"idTagInfo": {"status": "Accepted"}},
        "StartTransaction": {"transactionId": 1, "idTagInfo": {"status": "Accepted"}},
        "StopTransaction": {},
        "MeterValues": {},
        "StatusNotification": {},
    }.get(action, {})


@dataclass(frozen=True)
class ErrorReply:
    """What a FakeCharger responder returns to answer with a CallError."""

    code: str = "NotSupported"
    description: str = ""


class FakeCsms:
    """Accepts proxy connections; answers Calls via `responder` (None = stay silent)."""

    def __init__(self, responder: Responder = default_responder):
        self.responder = responder
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.replies: list[list[Any]] = []
        self.paths: list[str] = []
        self.auth_headers: list[str | None] = []
        self.connection: ServerConnection | None = None
        self.connected = asyncio.Event()
        self._server: Server | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._new_frame = asyncio.Event()

    async def start(self, port: int = 0) -> FakeCsms:
        self._server = await serve(self._handle, "127.0.0.1", port, subprotocols=["ocpp1.6"])
        return self

    @property
    def port(self) -> int:
        return self._server.sockets[0].getsockname()[1]

    @property
    def url(self) -> str:
        return f"ws://127.0.0.1:{self.port}/ocpp"

    async def stop(self) -> None:
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    async def drop_connection(self) -> None:
        if self.connection:
            await self.connection.close()

    async def _handle(self, ws: ServerConnection) -> None:
        self.paths.append(ws.request.path)
        self.auth_headers.append(ws.request.headers.get("Authorization"))
        self.connection = ws
        self.connected.set()
        try:
            async for text in ws:
                frame = json.loads(text)
                if frame[0] == 2:
                    self.calls.append((frame[2], frame[3]))
                    payload = self.responder(frame[2], frame[3])
                    if payload is not None:
                        await ws.send(json.dumps([3, frame[1], payload]))
                else:
                    self.replies.append(frame)
                    future = self._pending.pop(frame[1], None)
                    if future and not future.done():
                        future.set_result(frame)
                self._new_frame.set()
        finally:
            if self.connection is ws:
                self.connection = None
                self.connected.clear()

    async def send_raw(self, text: str) -> None:
        await self.connection.send(text)

    async def call(self, action: str, payload: dict[str, Any], message_id: str | None = None) -> list[Any]:
        """Send a command to the charger (through the proxy) and wait for the reply frame."""
        message_id = message_id or str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self.connection.send(json.dumps([2, message_id, action, payload]))
        return await asyncio.wait_for(future, 5)

    def actions(self) -> list[str]:
        return [action for action, _ in self.calls]

    async def wait_for_call(self, action: str, count: int = 1, timeout: float = 5) -> list[dict[str, Any]]:
        async def matching() -> list[dict[str, Any]]:
            while True:
                found = [p for a, p in self.calls if a == action]
                if len(found) >= count:
                    return found
                self._new_frame.clear()
                await self._new_frame.wait()

        return await asyncio.wait_for(matching(), timeout)


class FakeCharger:
    """Connects to the proxy like a charger would; answers commands via `responder`
    (an ErrorReply answers with a CallError, None stays silent)."""

    def __init__(self, ws: ClientConnection, responder: Responder):
        self.ws = ws
        self.responder = responder
        self.received_calls: list[list[Any]] = []
        self.frames: list[list[Any]] = []  # every frame received, calls included
        self._pending: dict[str, asyncio.Future] = {}
        self._reader = asyncio.create_task(self._read())

    @classmethod
    async def connect(
        cls,
        url: str,
        charger_id: str,
        password: str | None = None,
        responder: Responder = lambda action, payload: {"status": "Accepted"},
    ) -> FakeCharger:
        token = base64.b64encode(f"{charger_id}:{password or ''}".encode()).decode()
        ws = await connect(
            f"{url}/{charger_id}",
            subprotocols=["ocpp1.6"],
            additional_headers={"Authorization": f"Basic {token}"},
        )
        return cls(ws, responder)

    async def _read(self) -> None:
        async for text in self.ws:
            frame = json.loads(text)
            self.frames.append(frame)
            if frame[0] == 2:
                self.received_calls.append(frame)
                answer = self.responder(frame[2], frame[3])
                if isinstance(answer, ErrorReply):
                    await self.ws.send(json.dumps([4, frame[1], answer.code, answer.description, {}]))
                elif answer is not None:  # None: stay silent
                    await self.ws.send(json.dumps([3, frame[1], answer]))
            else:
                future = self._pending.pop(frame[1], None)
                if future and not future.done():
                    future.set_result(frame)

    async def call(self, action: str, payload: dict[str, Any], timeout: float = 5) -> list[Any]:
        message_id = str(uuid.uuid4())
        future = asyncio.get_running_loop().create_future()
        self._pending[message_id] = future
        await self.ws.send(json.dumps([2, message_id, action, payload]))
        return await asyncio.wait_for(future, timeout)

    async def send_raw(self, text: str) -> None:
        await self.ws.send(text)

    async def close(self) -> None:
        await self.ws.close()
        self._reader.cancel()


async def http_request(
    port: int, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None
) -> tuple[int, Any]:
    """A bare-bones HTTP client for the debug endpoint: JSON body in (unless a str/bytes), JSON out."""
    payload = body if isinstance(body, bytes) else b"" if body is None else json.dumps(body).encode()
    sent = {"Host": "localhost", "Content-Length": str(len(payload))}
    if payload:
        sent["Content-Type"] = "application/json"
    sent.update(headers or {})
    head = f"{method} {path} HTTP/1.1\r\n" + "".join(f"{k}: {v}\r\n" for k, v in sent.items()) + "\r\n"
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(head.encode() + payload)
        response = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
    status_line, _, rest = response.partition(b"\r\n")
    return int(status_line.split(b" ")[1]), json.loads(rest.partition(b"\r\n\r\n")[2])
