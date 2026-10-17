"""The monitor never slows the relay (docs/plans/flow-monitor.md, "Performance budget" and "Verification").

The default suite stresses it: a store whose every write takes 0.5 s (far slower than events come in), a live
client that never reads, and a burst of 1000 relayed MeterValues. The relay must finish, no loop callback from monitor
code may take 5 ms or more, and the events the monitor could not keep must show up as a gap and in the drop
counters.

``-m perf`` compares the relay's p99 round trip with the monitor on and off, back to back with no idle time.
"""

from __future__ import annotations

import asyncio
import logging
import re
import statistics
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from conftest import CHARGER_ID, make_raw_config
from fakes import FakeCharger, eventually, http_exchange, with_transaction

from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor import writer as writer_module
from ocpp_2w_proxy.server import ProxyServer

SLOW_WRITE_SECONDS = 0.5
SLOW_CALLBACK_SECONDS = 0.005
BURST = 1000
# An asyncio "Executing <handle> took" warning names the task (if any), then the file its code runs in.
TASK_NAME = re.compile(r"Executing <Task \w+ name='([^']*)'")
FIRST_SOURCE = re.compile(r" at (/\S+?):\d+")
MONITOR_TASK_PREFIX = "monitor-"
MONITOR_PATHS = ("/ocpp_2w_proxy/monitor/", "/ocpp_2w_proxy/minihttp.py", "/ocpp_2w_proxy/flow.py")


def meter_values(i: int) -> dict:
    return {"connectorId": 1, "transactionId": 1, "meterValue": [{"sampledValue": [{"value": str(i), "unit": "Wh"}]}]}


def slow_write_batch(conn, batch):
    time.sleep(SLOW_WRITE_SECONDS)  # in the write worker's thread, as a stalled disk would
    db.write_batch(conn, batch)


@pytest.fixture
async def debug_loop():
    """The running loop in debug mode, warning about every callback that takes 5 ms or more."""
    loop = asyncio.get_running_loop()
    loop.set_debug(True)
    loop.slow_callback_duration = SLOW_CALLBACK_SECONDS
    yield loop
    loop.set_debug(False)


async def never_reading_live_client(port: int) -> asyncio.StreamWriter:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
    await writer.drain()
    return writer


async def links(port: int) -> dict:
    reply = await http_exchange(port, "GET", "/api/links")
    assert reply.status == 200
    return reply.json()


async def test_a_stalled_store_and_a_stuck_client_never_slow_the_relay(
    start_proxy, proxies, primary, secondary, monkeypatch, debug_loop, caplog
):
    """MeterValues the secondary gets with its own transaction id: the frames that cost the monitor the most."""
    monkeypatch.setattr(writer_module, "write_batch", slow_write_batch)
    secondary.responder = with_transaction(77)
    url = await start_proxy(primary.url, secondary.url, monitor={})
    port = proxies[0].monitor_port
    stuck = await never_reading_live_client(port)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await secondary.connected.wait()
    await charger.call("StartTransaction", {"connectorId": 1, "idTag": "04A1B2C3D4", "meterStart": 0, "timestamp": "t"})
    await eventually(lambda: [action for action, _ in secondary.calls] == ["StartTransaction"])

    with caplog.at_level(logging.WARNING, logger="asyncio"):
        replies = await asyncio.wait_for(
            asyncio.gather(*(charger.call("MeterValues", meter_values(i), timeout=30) for i in range(BURST))), 30
        )
    slow_monitor_callbacks = [record.getMessage() for record in caplog.records if is_slow_monitor_callback(record)]

    assert len(replies) == BURST and all(reply[0] == 3 for reply in replies)
    assert slow_monitor_callbacks == []
    await eventually(lambda: any(payload["transactionId"] == 77 for _, payload in secondary.calls[1:]))
    charger_monitor = (await links(port))["chargers"][0]["monitor"]
    assert charger_monitor["dropped"] > 0 and charger_monitor["last_drop_at"] is not None
    gaps = await until_gap_stored(port)
    assert gaps[0]["charger_id"] == CHARGER_ID and gaps[0]["dropped"] > 0
    monkeypatch.setattr(writer_module, "write_batch", db.write_batch)  # the store recovers: teardown flushes quickly
    stuck.close()
    await charger.close()


def is_slow_monitor_callback(record: logging.LogRecord) -> bool:
    message = record.getMessage()
    if record.name != "asyncio" or not message.startswith("Executing <"):
        return False
    task = TASK_NAME.match(message)
    source = FIRST_SOURCE.search(message)
    return bool(task and task[1].startswith(MONITOR_TASK_PREFIX)) or bool(
        source and any(part in source[1] for part in MONITOR_PATHS)
    )


@pytest.mark.parametrize(
    ("message", "monitor"),
    [
        (
            "Executing <Task pending name='monitor-writer' coro=<MonitorWriter._run() running at "
            "/app/ocpp_2w_proxy/monitor/writer.py:99> wait_for=<Future created at /x/relay.py:1> took 0.012 seconds",
            True,
        ),
        (
            "Executing <Task pending name='Task-9' coro=<_Connections.handle() running at "
            "/app/ocpp_2w_proxy/minihttp.py:110> took 0.006 seconds",
            True,
        ),
        (
            "Executing <Handle Subscription.offer() at /app/ocpp_2w_proxy/monitor/live_hub.py:55> took 0.007 seconds",
            True,
        ),
        (
            "Executing <Task pending name='charger' coro=<ChargerSession._read_charger() running at "
            "/app/ocpp_2w_proxy/session.py:151> wait_for=<Future created at /app/ocpp_2w_proxy/monitor/x.py:1> "
            "took 0.009 seconds",
            False,
        ),
    ],
)
def test_slow_callbacks_are_attributed_by_task_name_and_source(message, monitor):
    record = logging.LogRecord("asyncio", logging.WARNING, "", 0, message, None, None)

    assert is_slow_monitor_callback(record) is monitor


async def until_gap_stored(port: int, timeout: float = 15) -> list[dict]:
    """The gap row is written behind the slow writes already queued."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not (gaps := (await links(port))["gaps"]):
        assert loop.time() < deadline, "no gap row was stored"
        await asyncio.sleep(0.2)
    return gaps


# --- -m perf ---------------------------------------------------------------------------------

ROUND_TRIPS = 2000
BLOCKS = 4
WARM_UP = 200
# The monitor-on proxy's writer finishes its last batch before the next monitor-off block starts.
DRAIN_SECONDS = 0.3
P99_MARGIN_SECONDS = 0.001


@asynccontextmanager
async def relayed_charger(primary_url: str, state_dir, monitor: bool) -> AsyncIterator[FakeCharger]:
    raw = make_raw_config(primary_url, None, state_dir, **({"monitor": {}} if monitor else {}))
    proxy = ProxyServer(parse(raw, {}))
    server = await proxy.start()
    charger = None
    try:
        url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ocpp"
        charger = await FakeCharger.connect(url, CHARGER_ID)
        for _ in range(WARM_UP):
            await charger.call("Heartbeat", {})
        yield charger
    finally:
        if charger is not None:
            await charger.close()
        await proxy.close()


async def back_to_back(charger: FakeCharger, count: int) -> list[float]:
    """Round trips with no pause in between: the loop is never idle, so every bit of monitor work shows."""
    durations = []
    for _ in range(count):
        started = time.perf_counter()
        await charger.call("Heartbeat", {})
        durations.append(time.perf_counter() - started)
    return durations


def p99(durations: list[float]) -> float:
    return statistics.quantiles(durations, n=100)[98]


@pytest.mark.perf
async def test_the_monitor_adds_at_most_a_millisecond_to_the_relay_p99(primary, tmp_path, caplog):
    """Monitor off and on in alternating blocks, so a noisy moment on the machine hits both sides alike."""
    caplog.set_level(logging.WARNING)  # per-frame INFO logging would dominate both runs
    off: list[float] = []
    on: list[float] = []
    async with (
        relayed_charger(primary.url, tmp_path / "off", monitor=False) as plain,
        relayed_charger(primary.url, tmp_path / "on", monitor=True) as monitored,
    ):
        for _ in range(BLOCKS):
            off += await back_to_back(plain, ROUND_TRIPS // BLOCKS)
            on += await back_to_back(monitored, ROUND_TRIPS // BLOCKS)
            await asyncio.sleep(DRAIN_SECONDS)

    report = f"p99 off {p99(off) * 1e3:.3f} ms, on {p99(on) * 1e3:.3f} ms"
    print(report)
    assert p99(on) <= p99(off) + P99_MARGIN_SECONDS, report
