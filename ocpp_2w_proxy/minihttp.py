"""A deliberately small, hardened HTTP/1.1 subset for the debug endpoint: JSON in, JSON out.

GET and POST only, one request per connection (`Connection: close`), a Content-Length body
(no chunked encoding), and hard limits on header size, header count, body size, read time and
concurrent connections. Anything beyond that is refused rather than interpreted.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

logger = logging.getLogger(__name__)

MAX_HEADER_BYTES = 16 * 1024
MAX_HEADERS = 32
MAX_BODY_BYTES = 64 * 1024
READ_TIMEOUT_SECONDS = 10
MAX_CONNECTIONS = 8

METHODS = frozenset({"GET", "POST"})
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
CONTENT_LENGTH = re.compile(r"^[0-9]{1,9}$")


@dataclass(frozen=True)
class Request:
    method: str
    path: str  # without the query string
    headers: dict[str, str]  # names lowercased
    body: bytes


@dataclass(frozen=True)
class Response:
    status: int
    body: Any  # JSON-serializable


Handler = Callable[[Request], Awaitable[Response]]


class HttpError(Exception):
    """A request refused with this status; `code` is the machine-readable reason."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def serve(handler: Handler, host: str, port: int) -> asyncio.Server:
    connections = _Connections(handler)
    return await asyncio.start_server(connections.handle, host, port, limit=MAX_HEADER_BYTES)


class _Connections:
    def __init__(self, handler: Handler):
        self._handler = handler
        self._open = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if self._open >= MAX_CONNECTIONS:
                await _write(writer, _error(HttpError(503, "too_many_connections", "too many open connections")))
                return
            self._open += 1
            try:
                await _write(writer, await self._respond(reader))
            finally:
                self._open -= 1
        except ConnectionError, asyncio.IncompleteReadError:
            pass  # the client went away
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def _respond(self, reader: asyncio.StreamReader) -> Response:
        try:
            request = await asyncio.wait_for(_read_request(reader), READ_TIMEOUT_SECONDS)
        except TimeoutError:
            return _error(HttpError(408, "request_timeout", "the request was not received in time"))
        except HttpError as exc:
            return _error(exc)
        try:
            return await self._handler(request)
        except HttpError as exc:
            return _error(exc)
        except Exception:
            logger.exception("debug request %s %s failed", request.method, request.path)
            return _error(HttpError(500, "internal_error", "the request could not be handled"))


async def _read_request(reader: asyncio.StreamReader) -> Request:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise HttpError(431, "headers_too_large", f"the headers exceed {MAX_HEADER_BYTES} bytes") from None
    method, path = _parse_request_line(head)
    headers = _parse_headers(head)
    if "transfer-encoding" in headers:
        raise HttpError(501, "not_implemented", "chunked and other transfer encodings are not supported")
    return Request(method, path, headers, await _read_body(reader, method, headers))


def _parse_request_line(head: bytes) -> tuple[str, str]:
    line = head.split(b"\r\n", 1)[0].decode("latin-1")
    parts = line.split(" ")
    if len(parts) != 3 or parts[2] not in ("HTTP/1.0", "HTTP/1.1"):
        raise HttpError(400, "bad_request", "malformed request line")
    method, target, _ = parts
    if method not in METHODS:
        raise HttpError(501, "not_implemented", f"method {method[:16]!r} is not supported")
    if not target.startswith("/"):
        raise HttpError(400, "bad_request", "the request target must be an absolute path")
    return method, target.split("?", 1)[0]


def _parse_headers(head: bytes) -> dict[str, str]:
    lines = head.decode("latin-1").split("\r\n")[1:-2]  # minus the request line and the blank end
    if len(lines) > MAX_HEADERS:
        raise HttpError(431, "too_many_headers", f"more than {MAX_HEADERS} headers")
    headers: dict[str, str] = {}
    for line in lines:
        name, separator, value = line.partition(":")
        if not separator or not HEADER_NAME.match(name):
            raise HttpError(400, "bad_request", "malformed header line")
        name = name.lower()
        if name in headers:
            raise HttpError(400, "bad_request", f"duplicate {name} header")
        headers[name] = value.strip()
    return headers


async def _read_body(reader: asyncio.StreamReader, method: str, headers: dict[str, str]) -> bytes:
    length = headers.get("content-length")
    if length is None:
        if method == "POST":
            raise HttpError(411, "length_required", "a POST needs a Content-Length header")
        return b""
    if not CONTENT_LENGTH.match(length):
        raise HttpError(400, "bad_request", "invalid Content-Length")
    if int(length) > MAX_BODY_BYTES:
        raise HttpError(413, "body_too_large", f"the body exceeds {MAX_BODY_BYTES} bytes")
    return await reader.readexactly(int(length))


def _error(error: HttpError) -> Response:
    return Response(error.status, {"error": error.code, "message": error.message})


async def _write(writer: asyncio.StreamWriter, response: Response) -> None:
    body = json.dumps(response.body).encode()
    head = (
        f"HTTP/1.1 {response.status} {HTTPStatus(response.status).phrase}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(head.encode("ascii") + body)
    await writer.drain()
