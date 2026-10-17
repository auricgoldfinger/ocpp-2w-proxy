"""The Flow Monitor inside a running proxy: real chargers and backends (fakes), read back over its HTTP API."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from collections.abc import Callable
from typing import Any

import pytest
from conftest import CHARGER_ID
from fakes import FakeCharger, FakeCsms, http_exchange, open_event_stream, with_transaction

from ocpp_2w_proxy.flow import FanOut
from ocpp_2w_proxy.monitor import payload_blob
from ocpp_2w_proxy.traffic_log import TrafficLog

START = {"connectorId": 1, "idTag": "04A2B3C4D5", "meterStart": 0, "timestamp": "2026-01-01T10:00:00Z"}
METER_VALUES = {"connectorId": 1, "transactionId": 100, "meterValue": []}


@pytest.fixture
async def stats():
    """A second secondary backend."""
    csms = await FakeCsms().start()
    yield csms
    await csms.stop()


async def exchanges(port: int, query: str = "") -> list[dict[str, Any]]:
    reply = await http_exchange(port, "GET", f"/api/exchanges?{query}")
    assert reply.status == 200
    return reply.json()["exchanges"]


async def until_listed(
    port: int, query: str, matches: Callable[[dict[str, Any]], bool], timeout: float = 5
) -> dict[str, Any]:
    """The newest exchange matching ``query`` once ``matches`` holds for it; the writer batches, so poll."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        listed = await exchanges(port, query)
        if listed and matches(listed[0]):
            return listed[0]
        if loop.time() > deadline:
            raise AssertionError(f"no exchange for {query!r} matched; last seen: {listed}")
        await asyncio.sleep(0.05)


def leg(exchange: dict[str, Any], peer: str) -> dict[str, Any]:
    return next(leg for leg in exchange["legs"] if leg["peer"] == peer)


def settled(*peers: str) -> Callable[[dict[str, Any]], bool]:
    def check(exchange):
        legs = {leg["peer"]: leg for leg in exchange["legs"]}
        return all(peer in legs and legs[peer]["reply"] is not None for peer in peers)

    return check


async def test_without_the_monitor_the_flow_is_the_plain_traffic_log(start_proxy, proxies, primary):
    await start_proxy(primary.url)

    proxy = proxies[0]
    assert type(proxy.context_for(CHARGER_ID).traffic) is TrafficLog
    assert proxy.monitor_port is None


async def test_with_the_monitor_the_flow_fans_out_and_the_ui_is_served(start_proxy, proxies, primary):
    await start_proxy(primary.url, monitor={})

    proxy = proxies[0]
    assert isinstance(proxy.context_for(CHARGER_ID).traffic, FanOut)
    reply = await http_exchange(proxy.monitor_port, "GET", "/api/config")
    assert (reply.status, reply.json()["chargers"]) == (200, [CHARGER_ID])


async def test_meter_values_show_each_leg_the_tx_rewrite_and_who_was_not_sent(
    start_proxy, proxies, primary, secondary, stats
):
    primary.responder = with_transaction(100)
    secondary.responder = with_transaction(9)
    url = await start_proxy(
        primary.url, [secondary.url, stats.url], secondary=[{}, {"forward_actions": ["StatusNotification"]}], monitor={}
    )
    port = proxies[0].monitor_port
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()
    await charger.call("StartTransaction", START)
    await secondary.wait_for_call("StartTransaction")
    await asyncio.sleep(0.05)  # the secondary's StartTransaction.conf links 100 to 9

    await charger.call("MeterValues", METER_VALUES)

    exchange = await until_listed(port, "action=MeterValues", settled("primary", "tap"))
    assert (exchange["origin"], exchange["outcome"], exchange["not_sent"]) == ("charger", "delivered", ["stats"])
    assert {leg["peer"]: (leg["outcome"], leg["rewritten"]) for leg in exchange["legs"]} == {
        "primary": ("delivered", []),
        "tap": ("delivered", [{"field": "tx", "from": 100, "to": 9}]),
    }
    await charger.close()


async def test_a_secondary_command_the_policy_answers_is_blocked_at_the_proxy(start_proxy, proxies, primary, secondary):
    url = await start_proxy(
        primary.url, secondary.url, secondary={"policy": {"actions": {"SetChargingProfile": "answer"}}}, monitor={}
    )
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()

    await secondary.call("SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {}})

    exchange = await until_listed(proxies[0].monitor_port, "action=SetChargingProfile", settled("charger"))
    assert (exchange["origin"], exchange["outcome"], exchange["problem"]) == ("tap", "answered_by_proxy", False)
    to_charger = leg(exchange, "charger")
    answer = (to_charger["replied_by"], to_charger["reply"], to_charger["reason"])
    assert answer == ("proxy", "result", "POLICY_ANSWERED")
    assert charger.received_calls == []
    await charger.close()


async def test_a_call_queued_in_a_primary_outage_is_delivered_after_the_reconnect(start_proxy, proxies, primary):
    port = primary.port
    url = await start_proxy(primary.url, monitor={})
    monitor_port = proxies[0].monitor_port
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await primary.stop()

    await charger.call("MeterValues", METER_VALUES)

    queued = await until_listed(monitor_port, "action=MeterValues", lambda e: leg(e, "primary")["outcome"] == "queued")
    assert (queued["outcome"], leg(queued, "primary")["reason"]) == ("queued", "QUEUED")

    recovered = await FakeCsms().start(port)
    try:
        await recovered.wait_for_call("MeterValues")
        delivered = await until_listed(monitor_port, "action=MeterValues", settled("primary"))
        primary_leg = leg(delivered, "primary")
        assert (delivered["id"], primary_leg["outcome"], primary_leg["reason"], primary_leg["replied_by"]) == (
            queued["id"], "delivered", "QUEUED", "peer"
        )  # fmt: skip
    finally:
        await charger.close()
        await recovered.stop()


async def test_the_live_stream_delivers_each_exchange_summary(start_proxy, proxies, primary):
    url = await start_proxy(primary.url, monitor={})
    charger = await FakeCharger.connect(url, CHARGER_ID)
    reader, writer, head = await open_event_stream(proxies[0].monitor_port)
    try:
        assert "200" in head.split("\r\n")[0] and "text/event-stream" in head

        await charger.call("Heartbeat", {})

        summary = await asyncio.wait_for(next_exchange_event(reader, "Heartbeat"), 5)
        assert (summary["charger_id"], summary["origin"]) == (CHARGER_ID, "charger")
    finally:
        writer.close()
        await charger.close()


async def next_exchange_event(reader: asyncio.StreamReader, action: str) -> dict[str, Any]:
    while True:
        frame = (await reader.readuntil(b"\n\n")).decode()
        lines = frame.strip().split("\n")
        if lines[0] != "event: exchange":
            continue
        summary = json.loads("".join(line.removeprefix("data: ") for line in lines[1:]))
        if summary["action"] == action:
            return summary


async def test_no_unmasked_id_tag_reaches_the_monitor_store(start_proxy, proxies, primary, tmp_path):
    primary.responder = with_transaction(100)
    url = await start_proxy(primary.url, monitor={})
    port = proxies[0].monitor_port
    charger = await FakeCharger.connect(url, CHARGER_ID)

    await charger.call("Authorize", {"idTag": START["idTag"]})
    await charger.call("StartTransaction", START)

    await until_listed(port, "action=Authorize", settled("primary"))
    started = await until_listed(port, "action=StartTransaction", settled("primary"))
    assert started["id_tag"] != START["idTag"]
    secret = START["idTag"].encode()
    for path in tmp_path.glob("monitor.sqlite*"):  # the database and its WAL
        assert secret not in path.read_bytes(), path.name
    for payload in stored_payloads(tmp_path / "monitor.sqlite"):
        assert secret not in payload
    detail = await http_exchange(port, "GET", f"/api/exchanges/{started['id']}")
    assert secret not in detail.body
    await charger.close()


def stored_payloads(path) -> list[bytes]:
    with sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True) as conn:
        rows = conn.execute("SELECT payload FROM frames WHERE payload IS NOT NULL").fetchall()
    assert rows  # at least the origin payloads are stored
    return [json.dumps(payload_blob.decode(blob)).encode() for (blob,) in rows]
