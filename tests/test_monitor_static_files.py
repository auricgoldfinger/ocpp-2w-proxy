"""The monitor UI's static whitelist, served over minihttp."""

from __future__ import annotations

import pytest
from fakes import http_exchange

from ocpp_2w_proxy.monitor.static_files import StaticFiles

INDEX = b"<!doctype html><title>Monitor</title>"
SECURITY_HEADERS = {
    "content-security-policy": "default-src 'self'",
    "x-content-type-options": "nosniff",
    "cache-control": "no-cache",
    "referrer-policy": "no-referrer",
}


@pytest.fixture
def static_root(tmp_path):
    root = tmp_path / "static"
    files = {
        "index.html": INDEX,
        "app.js": b"export const x = 1;",
        "css/monitor.css": b"body {}",
        "icons/logo.svg": b"<svg/>",
        "notes.md": b"# dev notes",
        "package.json": b"{}",
        "app.test.js": b"test()",
        ".eslintrc.js": b"module.exports = {}",
        "tests/projection.test.js": b"test()",
        "tests/helper.js": b"helper()",
        "fixtures/exchange.html": b"<p>fixture</p>",
        "node_modules/lib/index.js": b"lib()",
    }
    for name, body in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(body)
    (tmp_path / "config.py").write_text("SECRET = 1")
    return root


@pytest.fixture
async def port(static_root, serve_http):
    return await serve_http(StaticFiles(static_root).handle)


def test_only_ui_files_are_whitelisted(static_root):
    assert StaticFiles(static_root).paths == {
        "/",
        "/index.html",
        "/app.js",
        "/css/monitor.css",
        "/icons/logo.svg",
    }


@pytest.mark.parametrize("missing", [False, True])
def test_an_empty_or_missing_directory_serves_nothing(tmp_path, missing):
    root = tmp_path / "static"
    if not missing:
        root.mkdir()
    assert StaticFiles(root).paths == frozenset()


@pytest.mark.parametrize(
    ("path", "body", "content_type"),
    [
        ("/", INDEX, "text/html; charset=utf-8"),
        ("/index.html", INDEX, "text/html; charset=utf-8"),
        ("/app.js", b"export const x = 1;", "text/javascript; charset=utf-8"),
        ("/css/monitor.css", b"body {}", "text/css; charset=utf-8"),
        ("/icons/logo.svg", b"<svg/>", "image/svg+xml"),
    ],
)
async def test_a_whitelisted_file_is_served_with_its_type_and_security_headers(port, path, body, content_type):
    reply = await http_exchange(port, "GET", path)

    assert (reply.status, reply.body, reply.headers["content-type"]) == (200, body, content_type)
    assert SECURITY_HEADERS.items() <= reply.headers.items()


@pytest.mark.parametrize(
    "path",
    [
        "/missing.js",
        "/tests/helper.js",
        "/app.test.js",
        "/notes.md",
        "/../config.py",
        "/%2e%2e/config.py",
        "/css/../app.js",
        "/css/%2e%2e/%2e%2e/config.py",
        "//app.js",
        "/APP.JS",
    ],
)
async def test_anything_else_is_not_found(port, path):
    reply = await http_exchange(port, "GET", path)

    assert reply.status == 404
    assert b"SECRET" not in reply.body
    assert SECURITY_HEADERS.items() <= reply.headers.items()


async def test_only_get_is_allowed(port):
    reply = await http_exchange(port, "POST", "/index.html", b"")

    assert reply.status == 405
    assert SECURITY_HEADERS.items() <= reply.headers.items()
