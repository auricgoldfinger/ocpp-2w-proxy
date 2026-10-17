"""MonitorService: starts the store, writer and listener in order, and stays off when it cannot."""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace

import pytest
from conftest import CHARGER_ID, make_raw_config
from fakes import FakeProxy, http_exchange

from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.monitor.service import MonitorService
from ocpp_2w_proxy.ocpp import Call


def fake_proxy():
    idle = SimpleNamespace(connected=False, connected_since=None, queued_count=0)
    return FakeProxy({CHARGER_ID: SimpleNamespace(primary=idle, secondaries={}, traffic=None)}, {CHARGER_ID: 123.0})


def monitor_config(tmp_path, **monitor):
    raw = make_raw_config("ws://primary", None, tmp_path)
    raw["monitor"] = {"enabled": True, "port": 0, "db_path": str(tmp_path / "db" / "monitor.sqlite"), **monitor}
    return parse(raw, {})


@pytest.fixture
async def service(tmp_path):
    service = MonitorService(monitor_config(tmp_path), fake_proxy())
    await service.start()
    yield service
    await service.close()


async def test_serves_the_api_and_the_ui_once_started(service, tmp_path):
    config = await http_exchange(service.port, "GET", "/api/config")
    links = await http_exchange(service.port, "GET", "/api/links")
    page = await http_exchange(service.port, "GET", "/")

    assert (config.status, config.json()["primary"]) == (200, "primary")
    charger = links.json()["chargers"][0]
    assert (charger["id"], charger["connected"], charger["since"]) == (CHARGER_ID, True, 123.0)
    assert page.status == 200 and b"<html" in page.body.lower()
    assert (tmp_path / "db" / "monitor.sqlite").exists()


async def test_close_stops_the_listener(tmp_path):
    service = MonitorService(monitor_config(tmp_path), fake_proxy())
    await service.start()
    port = service.port

    await service.close()

    assert service.port is None
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
    await service.close()  # closing twice is harmless


async def test_an_unopenable_store_leaves_the_monitor_off(tmp_path, caplog):
    service = MonitorService(monitor_config(tmp_path, db_path=str(tmp_path)), fake_proxy())  # a directory

    with caplog.at_level(logging.ERROR):
        await service.start()

    assert service.port is None
    assert "runs without it" in caplog.text
    recorder = service.recorder_for(CHARGER_ID)
    for _ in range(5000):
        recorder.sent("primary", Call("w1", "Heartbeat", {}))
    assert recorder.dropped == 0  # detached: nothing is queued, so nothing overflows
    await service.close()


async def test_a_taken_port_leaves_the_monitor_off(tmp_path, caplog):
    blocker = await asyncio.start_server(lambda reader, writer: None, "127.0.0.1", 0)
    taken = blocker.sockets[0].getsockname()[1]
    service = MonitorService(monitor_config(tmp_path, port=taken), fake_proxy())
    try:
        with caplog.at_level(logging.ERROR):
            await service.start()
    finally:
        blocker.close()

    assert service.port is None
    assert "cannot listen" in caplog.text
