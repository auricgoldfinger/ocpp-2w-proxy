from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
from fakes import FakeCharger, FakeCsms, RecordingFlow, eventually

from ocpp_2w_proxy import backoff, minihttp, server
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.sqlite_worker import SqliteWorker
from ocpp_2w_proxy.server import ProxyServer

CHARGER_ID = "CH1"
START = {"connectorId": 1, "idTag": "04A2B3C4", "meterStart": 0, "timestamp": "2026-01-01T10:00:00Z"}

# Stable names for the secondary backends created in tests, in connection order.
SECONDARY_NAMES = ("tap", "stats", "beta", "gamma", "delta")


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backoff, "MIN_RETRY_DELAY", 0.05)
    monkeypatch.setattr(backoff, "MAX_RETRY_DELAY", 0.1)


@pytest.fixture
def flow(monkeypatch):
    """What the relay reports for every charger of a proxy started afterwards, recorded instead of logged."""
    recording = RecordingFlow()
    monkeypatch.setattr(server, "TrafficLog", lambda *args: recording)
    return recording


@pytest.fixture
async def primary():
    csms = await FakeCsms().start()
    yield csms
    await csms.stop()


@pytest.fixture
async def secondary():
    csms = await FakeCsms().start()
    yield csms
    await csms.stop()


def make_raw_config(
    primary_url: str, secondary_urls: str | list[str] | None, state_dir: Path, **overrides: Any
) -> dict:
    urls = [secondary_urls] if isinstance(secondary_urls, str) else list(secondary_urls or [])
    raw: dict[str, Any] = {
        "proxy": {"listen": "127.0.0.1", "port": 0, "state_dir": str(state_dir)},
        "logging": {"level": "DEBUG", "log_payloads": True},
        "chargers": [{"id": CHARGER_ID, **overrides.pop("charger", {})}],
        "primary": {"url": primary_url, "auth": "forward", "call_timeout": 3, **overrides.pop("primary", {})},
    }
    if "debug" in overrides:  # the endpoint is off unless a test asks for it; port 0 = any free port
        raw["debug"] = {"enabled": True, "port": 0, **overrides.pop("debug")}
    if "monitor" in overrides:  # likewise off unless asked for; its store defaults to <state_dir>/monitor.sqlite
        raw["monitor"] = {"enabled": True, "port": 0, **overrides.pop("monitor")}
    # One dict for every secondary, or a list with one dict per secondary.
    secondary_overrides = overrides.pop("secondary", {})
    if isinstance(secondary_overrides, dict):
        secondary_overrides = [secondary_overrides] * len(urls)
    entries = [
        {
            "name": SECONDARY_NAMES[i],
            "url": url,
            "auth": "basic",
            "password_env": "TAP_PASSWORD",
            "call_timeout": 2,
            **entry_overrides,
        }
        for i, (url, entry_overrides) in enumerate(zip(urls, secondary_overrides, strict=True))
    ]
    if entries:
        raw["secondary"] = entries
    return raw


@pytest.fixture
def proxies():
    """The ProxyServers started by start_proxy, for tests that reach into them."""
    return []


@pytest.fixture
async def start_proxy(tmp_path, proxies):
    async def _start(primary_url: str, secondary_urls: str | list[str] | None = None, environ=None, **overrides) -> str:
        environ = {"TAP_PASSWORD": "tap-secret", **(environ or {})}
        config = parse(make_raw_config(primary_url, secondary_urls, tmp_path, **overrides), environ)
        proxy = ProxyServer(config)
        server = await proxy.start()
        proxies.append(proxy)
        port = server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/ocpp"

    yield _start
    for proxy in proxies:
        await proxy.close()


@pytest.fixture
async def serve_http():
    """Starts minihttp servers for a handler and returns their port; closes them all after the test."""
    servers = []

    async def start(handler, **options) -> int:
        server = await minihttp.serve(handler, "127.0.0.1", 0, **options)
        servers.append(server)
        return server.sockets[0].getsockname()[1]

    yield start
    for started in servers:
        started.close()
        await started.wait_closed()


@pytest.fixture
def monitor_conn(tmp_path):
    """A writer connection to a fresh monitor store."""
    connection = sqlite3.connect(tmp_path / "monitor.sqlite")
    db.prepare_writer(connection)
    yield connection
    connection.close()


@pytest.fixture
async def monitor_store(tmp_path):
    """A writable SqliteWorker on a fresh monitor store."""
    worker = SqliteWorker(tmp_path / "monitor.sqlite", readonly=False)
    await worker.run(db.prepare_writer)
    yield worker
    await worker.close()


async def attached_charger(url, **kwargs) -> FakeCharger:
    charger = await FakeCharger.connect(url, CHARGER_ID, **kwargs)
    await charger.call("Heartbeat", {})  # the session (and its routing) is attached by now
    return charger


async def start_transaction(charger, proxy, **secondary_ids) -> list:
    """Starts a transaction; returns once each named secondary has linked its own id to the primary's."""
    reply = await charger.call("StartTransaction", START)
    transactions = proxy.context_for(CHARGER_ID).transactions
    primary_tx = reply[2]["transactionId"]
    await eventually(
        lambda: all(transactions.to_secondary(primary_tx, name) == tx for name, tx in secondary_ids.items())
    )
    return reply
