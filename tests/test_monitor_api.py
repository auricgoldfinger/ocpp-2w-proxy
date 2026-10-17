"""The monitor API over minihttp, against a real SQLite store, checked against docs/monitor_api.md."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from conftest import CHARGER_ID, make_raw_config
from fakes import http_exchange
from test_monitor_queries import blocked, heartbeat, meter_values, start, store

from ocpp_2w_proxy import minihttp
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.api import MonitorApi
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.live_hub import MAX_SUBSCRIBERS, LiveHub
from ocpp_2w_proxy.monitor.rows import LinkRow, WriteBatch
from ocpp_2w_proxy.monitor.sqlite_worker import SqliteWorker

NOW = 100_000.0
CP = "cp"  # the charger the stored rows of test_monitor_queries belong to
SUMMARY_KEYS = {
    "id",
    "charger_id",
    "origin",
    "action",
    "started_at",
    "last_at",
    "outcome",
    "problem",
    "incomplete",
    "transaction_id",
    "connector_id",
    "id_tag",
    "headline",
    "not_sent",
    "legs",
}
LEG_KEYS = {
    "peer",
    "wire_id",
    "outcome",
    "reason",
    "reason_text",
    "reply",
    "error_code",
    "replied_by",
    "sent_at",
    "replied_at",
    "rewritten",
    "late",
    "duplicate",
}
SNAPSHOT = {
    "chargers": [
        {
            "id": CHARGER_ID,
            "connected": True,
            "since": 99_000.0,
            "peers": [
                {"name": "primary", "role": "primary", "up": True, "since": 99_001.0, "queued": 0,
                 "messages_last_hour": 12}
            ],
            "monitor": {"dropped": 0, "last_drop_at": None},
        }
    ],
    "monitor": {"fanout_failures": 0, "dropped_batches": 0},
}  # fmt: skip


def write_store(conn):
    store(conn, 1, start(NOW - 500))
    store(conn, 2, meter_values(NOW - 400, 2), not_sent=("stats",))
    store(conn, 3, heartbeat(NOW - 300, 3))
    store(conn, 4, blocked(NOW - 200))
    store(conn, 5, meter_values(NOW - 100, 5))
    db.write_batch(
        conn,
        WriteBatch(
            links=[LinkRow(CP, "stats", 10.0, False), LinkRow(CP, "stats", NOW - 50, True, "boot replayed")],
            gaps=[Gap(CP, NOW - 40, NOW - 39, 37)],
        ),
    )


@pytest.fixture
async def monitor(tmp_path):
    raw = make_raw_config("ws://primary", ["ws://stats"], tmp_path, secondary={"forward_actions": ["MeterValues"]})
    raw["monitor"] = {"retention_days": 30}
    config = parse(raw, {"TAP_PASSWORD": "secret"})
    path = tmp_path / "monitor.sqlite"
    writer = SqliteWorker(path, readonly=False)
    reader = SqliteWorker(path, readonly=True)
    await writer.run(db.prepare_writer)
    await writer.run(write_store)
    hub = LiveHub()
    connected = {CHARGER_ID}
    api = MonitorApi(config, reader, hub, lambda: SNAPSHOT, lambda: connected, clock=lambda: NOW)
    server = await minihttp.serve(api.handle, "127.0.0.1", 0, max_connections=16)
    yield SimpleNamespace(port=server.sockets[0].getsockname()[1], hub=hub, connected=connected)
    hub.close()
    server.close()
    await server.wait_closed()
    await reader.close()
    await writer.close()


async def get(monitor, path, headers=None):
    return await http_exchange(monitor.port, "GET", path, headers=headers)


async def get_json(monitor, path):
    reply = await get(monitor, path)
    assert reply.status == 200, reply.body
    assert reply.headers["content-type"] == "application/json"
    return reply.json()


async def test_chargers_lists_the_configured_chargers_and_whether_each_is_connected(monitor):
    assert await get_json(monitor, "/api/chargers") == {"chargers": [{"id": CHARGER_ID, "connected": True}]}

    monitor.connected.clear()

    assert await get_json(monitor, "/api/chargers") == {"chargers": [{"id": CHARGER_ID, "connected": False}]}


async def test_config_names_the_peers_their_forwarded_actions_and_the_retention(monitor):
    assert await get_json(monitor, "/api/config") == {
        "primary": "primary",
        "secondaries": [{"name": "tap", "forward_actions": ["MeterValues"]}],
        "chargers": [CHARGER_ID],
        "retention_days": 30,
    }


async def test_exchanges_are_summaries_newest_first(monitor):
    body = await get_json(monitor, "/api/exchanges")

    assert body.keys() == {"exchanges", "next", "hidden"}
    assert [summary["id"] for summary in body["exchanges"]] == ["5", "4", "3", "2", "1"]
    assert body["next"] is None
    for summary in body["exchanges"]:
        assert summary.keys() == SUMMARY_KEYS
        for leg in summary["legs"]:
            assert leg.keys() == LEG_KEYS
    assert body["exchanges"][3]["not_sent"] == ["stats"]


async def test_exchange_filters_and_the_hidden_heartbeat_count(monitor):
    body = await get_json(monitor, "/api/exchanges?action=MeterValues&exclude=Heartbeat")
    assert [summary["id"] for summary in body["exchanges"]] == ["5", "2"]
    assert body["hidden"] == {"Heartbeat": 0}

    body = await get_json(monitor, f"/api/exchanges?exclude=Heartbeat&since={NOW - 350}")
    assert [summary["id"] for summary in body["exchanges"]] == ["5", "4"]
    assert body["hidden"] == {"Heartbeat": 1}


async def test_exchanges_page_through_the_next_cursor(monitor):
    seen, path = [], "/api/exchanges?limit=2"
    while path:
        body = await get_json(monitor, path)
        seen += [summary["id"] for summary in body["exchanges"]]
        path = body["next"] and f"/api/exchanges?limit=2&before={body['next']}"

    assert seen == ["5", "4", "3", "2", "1"]


async def test_exchange_detail_adds_payload_frames_and_decisions(monitor):
    detail = await get_json(monitor, "/api/exchanges/4")

    assert detail.keys() == SUMMARY_KEYS | {"payload", "frames", "decisions"}
    assert detail["payload"] == {"connectorId": 1, "csChargingProfiles": {}}
    assert detail["frames"][0].keys() == {
        "at", "peer", "dir", "msg_type", "wire_id", "payload", "same_as_origin", "error"
    }  # fmt: skip
    assert [decision["reason"] for decision in detail["decisions"]] == ["POLICY_ANSWERED"]
    assert detail["decisions"][0].keys() == {"at", "peer", "reason", "text", "detail"}


async def test_session_is_the_transaction_aggregate(monitor):
    session = await get_json(monitor, "/api/sessions/10216")

    assert session.keys() == {
        "transaction_id", "charger_id", "connector_id", "id_tag", "started_at", "stopped_at", "energy_wh",
        "exchanges", "problems",
    }  # fmt: skip
    assert (session["transaction_id"], session["charger_id"]) == (10216, CP)


async def test_links_merge_the_live_snapshot_with_the_last_day_of_history(monitor):
    assert await get_json(monitor, "/api/links") == {
        **SNAPSHOT,
        "history": [{"charger_id": CP, "peer": "stats", "at": NOW - 50, "up": True, "note": "boot replayed"}],
        "gaps": [{"charger_id": CP, "from_at": NOW - 40, "to_at": NOW - 39, "dropped": 37}],
    }


@pytest.mark.parametrize(
    "path",
    [
        "/api/exchanges/999",
        "/api/exchanges/99999999999999999999",
        "/api/exchanges/abc",
        "/api/sessions/4242",
        "/api/sessions/99999999999999999999",
        "/api/nothing",
        "/api/exchanges/",
    ],
)
async def test_unknown_is_404(monitor, path):
    reply = await get(monitor, path)
    assert (reply.status, reply.json()["error"]) == (404, "not_found")


@pytest.mark.parametrize(
    "query",
    ["charger=a&charger=b", "before=nonsense", "before=1.5:x", "limit=0", "outcome=lost", "tx=ten", "page=2"],
)
async def test_a_bad_query_is_400(monitor, query):
    reply = await get(monitor, f"/api/exchanges?{query}")
    assert (reply.status, reply.json()["error"]) == (400, "bad_request")


async def test_only_get_is_allowed(monitor):
    reply = await http_exchange(monitor.port, "POST", "/api/exchanges", {})
    assert (reply.status, reply.json()["error"]) == (405, "method_not_allowed")


@pytest.mark.parametrize("origin", ["http://evil.example", "http://localhost.evil.example", "null"])
async def test_a_foreign_origin_is_403(monitor, origin):
    reply = await get(monitor, "/api/config", {"Origin": origin})
    assert (reply.status, reply.json()["error"]) == (403, "origin_forbidden")


async def test_the_monitor_own_origin_is_allowed(monitor):
    reply = await get(monitor, "/api/config", {"Origin": "http://localhost"})  # Host is localhost
    assert reply.status == 200


@pytest.mark.parametrize("path", ["/api/config", "/api/exchanges", "/api/exchanges/999"])
async def test_api_json_is_never_sniffed_or_cached(monitor, path):
    reply = await get(monitor, path)
    assert reply.headers["x-content-type-options"] == "nosniff"
    assert reply.headers["cache-control"] == "no-store"


async def open_event_stream(port):
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /api/events HTTP/1.1\r\nHost: localhost\r\n\r\n")
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    return reader, writer, head.decode("ascii")


async def test_events_stream_what_the_hub_publishes(monitor):
    reader, writer, head = await open_event_stream(monitor.port)
    try:
        assert head.startswith("HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n")

        monitor.hub.publish("link", json.dumps({"charger_id": CP, "peer": "stats", "at": NOW, "up": True}).encode())

        frame = await asyncio.wait_for(reader.readuntil(b"\n\n"), 5)
    finally:
        writer.close()
    assert frame == b'event: link\ndata: {"charger_id": "cp", "peer": "stats", "at": 100000.0, "up": true}\n\n'


async def test_a_subscriber_beyond_the_limit_is_503(monitor):
    streams = [await open_event_stream(monitor.port) for _ in range(MAX_SUBSCRIBERS)]
    try:
        reply = await get(monitor, "/api/events")
    finally:
        for _, writer, _ in streams:
            writer.close()
    assert (reply.status, reply.json()["error"]) == (503, "too_many_subscribers")
