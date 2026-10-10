"""The debug endpoint's HTTP subset, exercised over raw sockets."""

from __future__ import annotations

import asyncio
import json

import pytest

from ocpp_2w_proxy import minihttp
from ocpp_2w_proxy.minihttp import HttpError, Request, Response


async def echo(request: Request) -> Response:
    if request.path == "/boom":
        raise RuntimeError("boom")
    if request.path == "/refuse":
        raise HttpError(404, "nope", "not here")
    return Response(
        200, {"method": request.method, "path": request.path, "headers": request.headers, "body": request.body.decode()}
    )


@pytest.fixture
async def port():
    server = await minihttp.serve(echo, "127.0.0.1", 0)
    yield server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()


async def exchange(port: int, raw: bytes) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    try:
        response = await asyncio.wait_for(reader.read(), 5)
    finally:
        writer.close()
    head, _, body = response.partition(b"\r\n\r\n")
    return int(head.split(b" ")[1]), json.loads(body)


def post(body: bytes = b"{}", headers: str = "", path: str = "/x") -> bytes:
    return f"POST {path} HTTP/1.1\r\nHost: h\r\n{headers}Content-Length: {len(body)}\r\n\r\n".encode() + body


async def test_a_get_and_a_post_reach_the_handler(port):
    status, body = await exchange(port, b"GET /a/b?x=1 HTTP/1.1\r\nHost: h\r\nOrigin: http://evil\r\n\r\n")
    assert (status, body["method"], body["path"], body["headers"]["origin"]) == (200, "GET", "/a/b", "http://evil")

    status, body = await exchange(port, post(b'{"a":1}'))
    assert (status, body["method"], body["body"]) == (200, "POST", '{"a":1}')


async def test_the_response_is_json_and_closes_the_connection(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET / HTTP/1.1\r\n\r\n")
    head = (await asyncio.wait_for(reader.read(), 5)).partition(b"\r\n\r\n")[0].decode()
    writer.close()
    assert "Content-Type: application/json" in head
    assert "Connection: close" in head


async def test_a_handler_refusal_keeps_its_status(port):
    assert await exchange(port, b"GET /refuse HTTP/1.1\r\n\r\n") == (404, {"error": "nope", "message": "not here"})


async def test_a_handler_crash_is_a_500_without_details(port):
    status, body = await exchange(port, b"GET /boom HTTP/1.1\r\n\r\n")
    assert (status, body["error"]) == (500, "internal_error")
    assert "boom" not in json.dumps(body)


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        (b"PUT / HTTP/1.1\r\nContent-Length: 0\r\n\r\n", 501),
        (b"GET / HTTP/2\r\n\r\n", 400),
        (b"GET\r\n\r\n", 400),
        (b"GET http://h/ HTTP/1.1\r\n\r\n", 400),
        (b"GET / HTTP/1.1\r\nno colon here\r\n\r\n", 400),
        (b"GET / HTTP/1.1\r\n folded: x\r\n\r\n", 400),
        (b"GET / HTTP/1.1\r\nA: 1\r\nA: 2\r\n\r\n", 400),
        (b"POST / HTTP/1.1\r\nHost: h\r\n\r\n", 411),
        (b"POST / HTTP/1.1\r\nContent-Length: -1\r\n\r\n", 400),
        (b"POST / HTTP/1.1\r\nContent-Length: 1e3\r\n\r\n", 400),
        (b"POST / HTTP/1.1\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n", 501),
        (b"POST / HTTP/1.1\r\nContent-Length: %d\r\n\r\n" % (minihttp.MAX_BODY_BYTES + 1), 413),
        (b"GET / HTTP/1.1\r\nX: " + b"a" * minihttp.MAX_HEADER_BYTES + b"\r\n\r\n", 431),
        (b"GET / HTTP/1.1\r\n" + b"".join(b"H%d: v\r\n" % i for i in range(minihttp.MAX_HEADERS + 1)) + b"\r\n", 431),
    ],
)
async def test_a_request_outside_the_subset_is_refused(port, raw, status):
    assert (await exchange(port, raw))[0] == status


async def test_a_body_at_the_limit_is_accepted(port):
    status, body = await exchange(port, post(b"x" * minihttp.MAX_BODY_BYTES))
    assert (status, len(body["body"])) == (200, minihttp.MAX_BODY_BYTES)


async def test_a_stalled_request_times_out(port, monkeypatch):
    monkeypatch.setattr(minihttp, "READ_TIMEOUT_SECONDS", 0.1)
    assert (await exchange(port, b"POST / HTTP/1.1\r\nContent-Length: 10\r\n\r\nabc"))[0] == 408


async def test_connections_beyond_the_limit_are_refused_with_503(port):
    held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(minihttp.MAX_CONNECTIONS)]
    await asyncio.sleep(0.1)  # let the server register them all

    assert (await exchange(port, b""))[0] == 503  # refused before it is even read

    for _, writer in held:
        writer.close()
