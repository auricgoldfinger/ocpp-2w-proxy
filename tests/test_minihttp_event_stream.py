"""The EventStream response of minihttp: Server-Sent Events over raw sockets."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest
from fakes import open_event_stream

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

    async def handle(self, request: Request) -> EventStream:
        return EventStream(self.stream())


@pytest.fixture
def source():
    return Source()


@pytest.fixture
async def port(serve_http, source):
    return await serve_http(source.handle)


async def test_the_stream_sends_sse_headers_and_each_chunk_until_the_source_ends(port, source):
    reader, writer, head = await open_event_stream(port)
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


async def test_an_idle_stream_is_kept_alive_with_ping_comments(port, source, monkeypatch):
    monkeypatch.setattr(minihttp, "PING_INTERVAL_SECONDS", 0.05)
    reader, writer, _ = await open_event_stream(port)

    assert await asyncio.wait_for(reader.readexactly(len(minihttp.PING) * 2), 5) == minihttp.PING * 2

    source.chunks.put_nowait(b"data: after\n\n")
    assert b"data: after\n\n" in await asyncio.wait_for(reader.readuntil(b"after\n\n"), 5)
    writer.close()


async def test_a_disconnecting_client_cancels_the_source(port, source):
    _, writer, _ = await open_event_stream(port)

    writer.close()

    await asyncio.wait_for(source.stopped.wait(), 5)


async def test_close_clients_ends_open_streams_on_shutdown(source):
    server = await minihttp.serve(source.handle, "127.0.0.1", 0)
    reader, writer, _ = await open_event_stream(server.sockets[0].getsockname()[1])

    server.close()
    server.close_clients()

    assert await asyncio.wait_for(reader.read(), 5) == b""
    await asyncio.wait_for(source.stopped.wait(), 5)
    await asyncio.wait_for(server.wait_closed(), 5)
    writer.close()


async def test_a_failing_source_is_logged_and_ends_the_stream(serve_http, caplog):
    async def failing() -> AsyncIterator[bytes]:
        yield b"data: 1\n\n"
        raise RuntimeError("boom")

    async def handler(request: Request) -> EventStream:
        return EventStream(failing())

    reader, writer, _ = await open_event_stream(await serve_http(handler))

    assert await asyncio.wait_for(reader.read(), 5) == b"data: 1\n\n"
    assert "event stream source failed" in caplog.text
    writer.close()
