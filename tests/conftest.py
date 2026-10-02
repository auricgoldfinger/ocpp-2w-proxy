from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fakes import FakeCsms

from ocpp_2w_proxy import secondary_channel
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.server import ProxyServer

CHARGER_ID = "CH1"


@pytest.fixture(autouse=True)
def fast_reconnect(monkeypatch):
    monkeypatch.setattr(secondary_channel, "MIN_RETRY_DELAY", 0.05)
    monkeypatch.setattr(secondary_channel, "MAX_RETRY_DELAY", 0.1)


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


def make_raw_config(primary_url: str, secondary_url: str | None, state_dir: Path, **overrides: Any) -> dict:
    raw: dict[str, Any] = {
        "proxy": {"listen": "127.0.0.1", "port": 0, "state_dir": str(state_dir)},
        "logging": {"level": "DEBUG", "log_payloads": True},
        "chargers": [{"id": CHARGER_ID, **overrides.pop("charger", {})}],
        "primary": {"url": primary_url, "auth": "forward", "call_timeout": 3, **overrides.pop("primary", {})},
    }
    if secondary_url:
        raw["secondary"] = {
            "url": secondary_url,
            "auth": "basic",
            "password_env": "TAP_PASSWORD",
            "call_timeout": 2,
            **overrides.pop("secondary", {}),
        }
    return raw


@pytest.fixture
def start_proxy(tmp_path):
    servers = []

    async def _start(primary_url: str, secondary_url: str | None, environ=None, **overrides) -> str:
        environ = {"TAP_PASSWORD": "tap-secret", **(environ or {})}
        config = parse(make_raw_config(primary_url, secondary_url, tmp_path, **overrides), environ)
        server = await ProxyServer(config).start()
        servers.append(server)
        port = server.sockets[0].getsockname()[1]
        return f"ws://127.0.0.1:{port}/ocpp"

    yield _start
    for server in servers:
        server.close()
