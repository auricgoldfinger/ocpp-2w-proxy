"""A deliberately small, hardened HTTP/1.1 subset for the local endpoints: JSON in, JSON or bytes out.

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
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qsl

logger = logging.getLogger(__name__)

MAX_HEADER_BYTES = 16 * 1024
MAX_HEADERS = 32
MAX_BODY_BYTES = 64 * 1024
MAX_QUERY_FIELDS = 32
READ_TIMEOUT_SECONDS = 10
DEFAULT_MAX_CONNECTIONS = 8
# A comment line this often keeps idle event streams open through proxies and reveals dead clients.
PING_INTERVAL_SECONDS = 15
PING = b": ping\n\n"

METHODS = frozenset({"GET", "POST"})
HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
CONTENT_LENGTH = re.compile(r"^[0-9]{1,9}$")


@dataclass(frozen=True)
class Request:
    method: str
    path: str  # without the query string
    headers: dict[str, str]  # names lowercased
    body: bytes
    query: Mapping[str, str] = field(default_factory=dict)  # each key at most once


@dataclass(frozen=True)
class Response:
    status: int
    body: Any  # JSON-serializable


@dataclass(frozen=True)
class Content:
    """A response whose body is already encoded, such as a static file or prebuilt JSON."""

    status: int
    body: bytes
    content_type: str
    headers: tuple[tuple[str, str], ...] = ()  # extra (name, value) pairs, e.g. security headers


@dataclass(frozen=True)
class EventStream:
    """A Server-Sent Events response: each chunk from `source` is written as is, until it ends.

    The stream also ends when the client goes away, and then `source` is cancelled or closed, so a
    generator's `finally` runs. Close lingering streams on shutdown with `asyncio.Server.close_clients()`.
    """

    source: AsyncIterator[bytes]


Reply = Response | Content | EventStream
Handler = Callable[[Request], Awaitable[Reply]]


class HttpError(Exception):
    """A request refused with this status; `code` is the machine-readable reason."""

    def __init__(self, status: int, code: str, message: str):
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


async def serve(
    handler: Handler, host: str, port: int, max_connections: int = DEFAULT_MAX_CONNECTIONS
) -> asyncio.Server:
    connections = _Connections(handler, max_connections)
    return await asyncio.start_server(connections.handle, host, port, limit=MAX_HEADER_BYTES)


class _Connections:
    def __init__(self, handler: Handler, max_connections: int):
        self._handler = handler
        self._max_connections = max_connections
        self._open = 0

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            if self._open >= self._max_connections:
                await _write(
                    writer, _error(HttpError(503, "too_many_connections", "too many open connections")), reader
                )
                return
            self._open += 1
            try:
                await _write(writer, await self._respond(reader), reader)
            finally:
                self._open -= 1
        except ConnectionError, asyncio.IncompleteReadError:
            pass  # the client went away
        finally:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()

    async def _respond(self, reader: asyncio.StreamReader) -> Reply:
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
            logger.exception("http request %s %s failed", request.method, request.path)
            return _error(HttpError(500, "internal_error", "the request could not be handled"))


async def _read_request(reader: asyncio.StreamReader) -> Request:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except asyncio.LimitOverrunError:
        raise HttpError(431, "headers_too_large", f"the headers exceed {MAX_HEADER_BYTES} bytes") from None
    method, target = _parse_request_line(head)
    path, _, raw_query = target.partition("?")
    query = _parse_query(raw_query)
    headers = _parse_headers(head)
    if "transfer-encoding" in headers:
        raise HttpError(501, "not_implemented", "chunked and other transfer encodings are not supported")
    return Request(method, path, headers, await _read_body(reader, method, headers), query)


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
    return method, target


def _parse_query(query: str) -> dict[str, str]:
    try:
        fields = parse_qsl(
            query, keep_blank_values=True, strict_parsing=True, errors="strict", max_num_fields=MAX_QUERY_FIELDS
        )
    except ValueError:
        raise HttpError(400, "bad_request", "malformed query string") from None
    parameters = dict(fields)
    if len(parameters) != len(fields):
        raise HttpError(400, "bad_request", "a query parameter appears more than once")
    return parameters


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


async def _write(writer: asyncio.StreamWriter, reply: Reply, reader: asyncio.StreamReader) -> None:
    match reply:
        case Response():
            await _write_content(writer, _as_json(reply))
        case Content():
            await _write_content(writer, reply)
        case EventStream():
            await _write_event_stream(writer, reply.source, reader)


def _as_json(response: Response) -> Content:
    return Content(response.status, json.dumps(response.body).encode(), "application/json")


async def _write_content(writer: asyncio.StreamWriter, content: Content) -> None:
    extra = "".join(f"{name}: {value}\r\n" for name, value in content.headers)
    head = (
        f"HTTP/1.1 {content.status} {HTTPStatus(content.status).phrase}\r\n"
        f"Content-Type: {content.content_type}\r\n"
        f"Content-Length: {len(content.body)}\r\n"
        f"{extra}"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(head.encode("ascii") + content.body)
    await writer.drain()


async def _write_event_stream(
    writer: asyncio.StreamWriter, source: AsyncIterator[bytes], reader: asyncio.StreamReader
) -> None:
    writer.write(
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n"
    )
    await writer.drain()
    client_gone = asyncio.create_task(_until_closed(reader))
    pending: asyncio.Task[bytes | None] | None = None
    try:
        while True:
            if pending is None:
                pending = asyncio.create_task(_next_chunk(source))
            await asyncio.wait({pending, client_gone}, timeout=PING_INTERVAL_SECONDS, return_when="FIRST_COMPLETED")
            if client_gone.done():
                return
            if pending.done():
                chunk, pending = pending.result(), None
                if chunk is None:
                    return
                writer.write(chunk)
            else:
                writer.write(PING)
            await writer.drain()
    finally:
        client_gone.cancel()
        await _stop(source, pending)


async def _until_closed(reader: asyncio.StreamReader) -> None:
    while await reader.read(1024):
        pass  # a client has nothing more to say on an event stream; discard it


async def _next_chunk(source: AsyncIterator[bytes]) -> bytes | None:
    """The next chunk, or None once the source has ended (or failed, which is logged)."""
    try:
        return await anext(source, None)
    except Exception:
        logger.exception("event stream source failed")
        return None


async def _stop(source: AsyncIterator[bytes], pending: asyncio.Task[bytes | None] | None) -> None:
    """Let the source clean up: cancel a read in progress, or close a generator suspended at a yield."""
    if pending is not None and not pending.done():
        pending.cancel()
        return
    aclose = getattr(source, "aclose", None)
    if aclose is not None:
        await aclose()
