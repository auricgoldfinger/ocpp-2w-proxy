"""The EventStream response of minihttp: Server-Sent Events over raw sockets."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from ocpp_2w_proxy import minihttp
from ocpp_2w_proxy.minihttp import EventStream, Request


class Source:
    """An event source the test feeds by hand, which records how it was stopped."""

    def __init__(self) -> None:
        self.chunks: asyncio.Queue[bytes | None] = asyncio.Queue()
        self.stopped = asyncio.Event()

    async def stream(self) -> AsyncIterator[bytes]:
        try:
            while (chunk := await self.chunks.get()) is not None:
                yield chunk
        finally:
            self.stopped.set()


@pytest.fixture
async def stream_server():
    source = Source()

    async def handler(request: Request) -> EventStream:
        return EventStream(source.stream())

    server = await minihttp.serve(handler, "127.0.0.1", 0)
    yield server, source
    server.close()
    await server.wait_closed()


async def subscribe(server: asyncio.Server) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    writer.write(b"GET /api/events HTTP/1.1\r\n\r\n")
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    return reader, writer, head.decode("ascii")


async def test_the_stream_sends_sse_headers_and_each_chunk_until_the_source_ends(stream_server):
    server, source = stream_server
    reader, writer, head = await subscribe(server)
    for chunk in (b"event: exchange\ndata: 1\n\n", b"event: link\ndata: 2\n\n", None):
        source.chunks.put_nowait(chunk)

    body = await asyncio.wait_for(reader.read(), 5)  # the server closes the connection
    writer.close()

    assert head.split("\r\n")[:4] == [
        "HTTP/1.1 200 OK",
        "Content-Type: text/event-stream",
        "Cache-Control: no-cache",
        "Connection: close",
    ]
    assert body == b"event: exchange\ndata: 1\n\nevent: link\ndata: 2\n\n"
    assert source.stopped.is_set()


async def test_an_idle_stream_is_kept_alive_with_ping_comments(stream_server, monkeypatch):
    monkeypatch.setattr(minihttp, "PING_INTERVAL_SECONDS", 0.05)
    server, source = stream_server
    reader, writer, _ = await subscribe(server)

    assert await asyncio.wait_for(reader.readexactly(len(minihttp.PING) * 2), 5) == minihttp.PING * 2

    source.chunks.put_nowait(b"data: after\n\n")
    assert b"data: after\n\n" in await asyncio.wait_for(reader.readuntil(b"after\n\n"), 5)
    writer.close()


async def test_a_disconnecting_client_cancels_the_source(stream_server):
    server, source = stream_server
    _, writer, _ = await subscribe(server)

    writer.close()

    await asyncio.wait_for(source.stopped.wait(), 5)


async def test_close_clients_ends_open_streams_on_shutdown(stream_server):
    server, source = stream_server
    reader, writer, _ = await subscribe(server)

    server.close()
    server.close_clients()

    assert await asyncio.wait_for(reader.read(), 5) == b""
    await asyncio.wait_for(source.stopped.wait(), 5)
    await asyncio.wait_for(server.wait_closed(), 5)
    writer.close()


async def test_a_failing_source_is_logged_and_ends_the_stream(caplog):
    async def failing() -> AsyncIterator[bytes]:
        yield b"data: 1\n\n"
        raise RuntimeError("boom")

    async def handler(request: Request) -> EventStream:
        return EventStream(failing())

    server = await minihttp.serve(handler, "127.0.0.1", 0)
    reader, writer, _ = await subscribe(server)

    assert await asyncio.wait_for(reader.read(), 5) == b"data: 1\n\n"
    assert "event stream source failed" in caplog.text
    writer.close()
    server.close()
    await server.wait_closed()
