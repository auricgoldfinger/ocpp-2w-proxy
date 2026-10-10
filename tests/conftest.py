from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fakes import FakeCsms

from ocpp_2w_proxy import backoff
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.server import ProxyServer

CHARGER_ID = "CH1"

# Stable names for the secondary backends created in tests, in connection order.
SECONDARY_NAMES = ("tap", "stats", "beta", "gamma", "delta")


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(backoff, "MIN_RETRY_DELAY", 0.05)
    monkeypatch.setattr(backoff, "MAX_RETRY_DELAY", 0.1)


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
    secondary_overrides = overrides.pop("secondary", {})
    entries = [
        {
            "name": SECONDARY_NAMES[i],
            "url": url,
            "auth": "basic",
            "password_env": "TAP_PASSWORD",
            "call_timeout": 2,
            **secondary_overrides,
        }
        for i, url in enumerate(urls)
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
