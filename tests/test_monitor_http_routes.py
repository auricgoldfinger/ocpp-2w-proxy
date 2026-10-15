import pytest
from fakes import http_exchange

from ocpp_2w_proxy import minihttp
from ocpp_2w_proxy.minihttp import Content, Request
from ocpp_2w_proxy.monitor.http_routes import MonitorRouter
from ocpp_2w_proxy.monitor.static_files import StaticFiles


async def api(request: Request) -> Content:
    return Content(200, b"api", "text/plain")


async def static(request: Request) -> Content:
    return Content(200, b"static", "text/plain")


@pytest.mark.parametrize(
    ("path", "handled_by"),
    [
        ("/api", b"api"),
        ("/api/", b"api"),
        ("/api/exchanges", b"api"),
        ("/api/exchanges/42", b"api"),
        ("/", b"static"),
        ("/index.html", b"static"),
        ("/apiary.js", b"static"),
        ("/static/api/x.js", b"static"),
    ],
)
async def test_api_paths_go_to_the_api_and_the_rest_to_the_static_files(path, handled_by):
    router = MonitorRouter(api, static)

    reply = await router.handle(Request("GET", path, {}, b""))

    assert reply.body == handled_by


async def test_both_sides_are_served_over_one_listener(tmp_path):
    (tmp_path / "index.html").write_bytes(b"<!doctype html>")
    router = MonitorRouter(api, StaticFiles(tmp_path).handle)
    server = await minihttp.serve(router.handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        replies = [await http_exchange(port, "GET", path) for path in ("/api/config", "/", "/api/../index.html")]
    finally:
        server.close()
        await server.wait_closed()

    assert [(reply.status, reply.body) for reply in replies] == [
        (200, b"api"),
        (200, b"<!doctype html>"),
        (200, b"api"),
    ]
