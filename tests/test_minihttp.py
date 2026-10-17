"""The debug endpoint's HTTP subset, exercised over raw sockets."""

from __future__ import annotations

import asyncio
import json

import pytest

from ocpp_2w_proxy import minihttp
from ocpp_2w_proxy.minihttp import Content, HttpError, Request, Response

STATIC = Content(
    200,
    b"<!doctype html>\n",
    "text/html; charset=utf-8",
    (("Content-Security-Policy", "default-src 'self'"), ("X-Content-Type-Options", "nosniff")),
)


async def echo(request: Request) -> Response | Content:
    if request.path == "/static":
        return STATIC
    if request.path == "/boom":
        raise RuntimeError("boom")
    if request.path == "/refuse":
        raise HttpError(404, "nope", "not here")
    return Response(
        200,
        {
            "method": request.method,
            "path": request.path,
            "query": dict(request.query),
            "headers": request.headers,
            "body": request.body.decode(),
        },
    )


@pytest.fixture
async def port(serve_http):
    return await serve_http(echo)


async def exchange_raw(port: int, raw: bytes) -> tuple[str, bytes]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(raw)
    try:
        response = await asyncio.wait_for(reader.read(), 5)
    finally:
        writer.close()
    head, _, body = response.partition(b"\r\n\r\n")
    return head.decode("ascii"), body


async def exchange(port: int, raw: bytes) -> tuple[int, dict]:
    head, body = await exchange_raw(port, raw)
    return int(head.split(" ")[1]), json.loads(body)


def post(body: bytes = b"{}", headers: str = "", path: str = "/x") -> bytes:
    return f"POST {path} HTTP/1.1\r\nHost: h\r\n{headers}Content-Length: {len(body)}\r\n\r\n".encode() + body


async def test_a_get_and_a_post_reach_the_handler(port):
    status, body = await exchange(port, b"GET /a/b?x=1 HTTP/1.1\r\nHost: h\r\nOrigin: http://evil\r\n\r\n")
    assert (status, body["method"], body["path"], body["headers"]["origin"]) == (200, "GET", "/a/b", "http://evil")

    status, body = await exchange(port, post(b'{"a":1}'))
    assert (status, body["method"], body["body"]) == (200, "POST", '{"a":1}')


@pytest.mark.parametrize(
    ("target", "query"),
    [
        ("/a", {}),
        ("/a?", {}),
        ("/a?x=1&y=two+words&z=%C3%A9", {"x": "1", "y": "two words", "z": "\u00e9"}),
        ("/a?empty=", {"empty": ""}),
    ],
)
async def test_the_query_string_is_parsed(port, target, query):
    status, body = await exchange(port, f"GET {target} HTTP/1.1\r\n\r\n".encode())
    assert (status, body["path"], body["query"]) == (200, "/a", query)


@pytest.mark.parametrize(
    "target",
    ["/a?x=1&x=2", "/a?x", "/a?x=1&&y=2", "/a?x=%ff", "/a?" + "&".join(f"k{i}=v" for i in range(33))],
)
async def test_a_malformed_or_ambiguous_query_is_refused(port, target):
    status, body = await exchange(port, f"GET {target} HTTP/1.1\r\n\r\n".encode())
    assert (status, body["error"]) == (400, "bad_request")


async def test_the_response_is_uncached_unsniffed_json_and_closes_the_connection(port):
    head, _ = await exchange_raw(port, b"GET / HTTP/1.1\r\n\r\n")
    assert {
        "Content-Type: application/json",
        "X-Content-Type-Options: nosniff",
        "Cache-Control: no-store",
        "Connection: close",
    } <= set(head.split("\r\n"))


async def test_content_is_sent_as_is_with_its_type_and_extra_headers(port):
    head, body = await exchange_raw(port, b"GET /static HTTP/1.1\r\n\r\n")
    lines = head.split("\r\n")
    assert lines[0] == "HTTP/1.1 200 OK"
    assert body == STATIC.body
    assert set(lines[1:]) == {
        "Content-Type: text/html; charset=utf-8",
        f"Content-Length: {len(STATIC.body)}",
        "Content-Security-Policy: default-src 'self'",
        "X-Content-Type-Options: nosniff",
        "Connection: close",
    }


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


@pytest.mark.parametrize(
    ("options", "limit"),
    [({}, minihttp.DEFAULT_MAX_CONNECTIONS), ({"max_connections": 2}, 2)],
    ids=["default", "configured"],
)
async def test_connections_beyond_the_limit_are_refused_with_503(serve_http, options, limit):
    port = await serve_http(echo, **options)
    held = [await asyncio.open_connection("127.0.0.1", port) for _ in range(limit)]
    await asyncio.sleep(0.1)  # let the server register them all

    assert (await exchange(port, b""))[0] == 503  # refused before it is even read

    for _, writer in held:
        writer.close()
