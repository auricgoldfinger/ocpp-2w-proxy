import itertools
import json
import sqlite3

import pytest

from ocpp_2w_proxy.monitor import db
from ocpp_2w_proxy.monitor.events import EventKind, FlowEvent
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.projection import ExchangeProjection
from ocpp_2w_proxy.monitor.queries import (
    MAX_LIMIT,
    ExchangeQuery,
    InvalidCursor,
    exchange_detail,
    links_report,
    list_exchanges,
    session_summary,
)
from ocpp_2w_proxy.monitor.rows import ExchangeRow, LinkRow, WriteBatch, decision_row, frame_row
from ocpp_2w_proxy.monitor.sqlite_worker import SqliteWorker
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason

CP = "cp"
METER = {
    "connectorId": 1,
    "transactionId": 10216,
    "meterValue": [{"sampledValue": [{"value": "1241921", "unit": "Wh"}]}],
}


def received(at, peer, message, charger=CP):
    return FlowEvent(EventKind.RECEIVED, at, charger, peer, message, None, None)


def sent(at, peer, message, charger=CP):
    return FlowEvent(EventKind.SENT, at, charger, peer, message, None, None)


def decided(at, peer, reason, detail=None, charger=CP):
    return FlowEvent(EventKind.DECIDED, at, charger, peer, None, reason, detail)


def store(conn, exchange_id, events, not_sent=()):
    """What the writer does: project, encode the frames against the origin payload, write one batch."""
    projection = ExchangeProjection(exchange_id)
    projection.not_sent = not_sent
    frames, decisions, origin = [], [], None
    for event in events:
        projection.apply(event)
        if event.kind is EventKind.DECIDED:
            decisions.append(decision_row(exchange_id, event))
            continue
        frames.append(frame_row(exchange_id, event, origin))
        if origin is None and isinstance(event.message, Call):
            origin = event.message.payload
    exchange = ExchangeRow.from_summary(projection.summary())
    db.write_batch(conn, WriteBatch(exchanges=[exchange], frames=frames, decisions=decisions))


def meter_values(at, exchange_id, charger=CP):
    call = Call(f"mv{exchange_id}", "MeterValues", METER)
    return [
        received(at, "charger", call, charger),
        sent(at + 0.01, "primary", call, charger),
        sent(at + 0.02, "ha", Call(f"ha{exchange_id}", "MeterValues", {**METER, "transactionId": 77}), charger),
        received(at + 0.03, "primary", CallResult(call.id, {}), charger),
    ]


def start(at):
    call = Call("st", "StartTransaction", {"connectorId": 1, "idTag": "***4F2A", "meterStart": 1234500})
    return [
        received(at, "charger", call),
        sent(at + 0.01, "primary", call),
        received(at + 0.02, "primary", CallResult("st", {"transactionId": 10216, "idTagInfo": {}})),
    ]


def stop(at):
    call = Call("sp", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921, "reason": "Local"})
    return [received(at, "charger", call), decided(at + 0.01, "primary", Reason.QUEUE_OVERFLOW)]


def blocked(at):
    call = Call("cmd-1", "SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {}})
    return [
        received(at, "ha", call),
        decided(at, "ha", Reason.POLICY_ANSWERED),
        sent(at, "ha", CallResult("cmd-1", {"status": "Accepted"})),
    ]


def refused(at):
    call = Call("cmd-2", "ChangeConfiguration", {"key": "x", "value": "y"})
    return [
        received(at, "ha", call),
        decided(at, "ha", Reason.POLICY_REFUSED),
        sent(at, "ha", CallError("cmd-2", "NotSupported", "no")),
    ]


def heartbeat(at, exchange_id):
    call = Call(f"hb{exchange_id}", "Heartbeat", {})
    return [received(at, "charger", call), sent(at, "primary", call)]


@pytest.fixture
def conn(tmp_path):
    connection = sqlite3.connect(tmp_path / "monitor.sqlite")
    db.prepare_writer(connection)
    yield connection
    connection.close()


@pytest.fixture
def populated(conn):
    store(conn, 1, start(100.0))
    store(conn, 2, meter_values(200.0, 2), not_sent=("stats",))
    store(conn, 3, heartbeat(250.0, 3))
    store(conn, 4, blocked(300.0))
    store(conn, 5, refused(400.0))
    store(conn, 6, meter_values(500.0, 6, charger="other"))
    store(conn, 7, stop(600.0))
    store(conn, 8, heartbeat(650.0, 8))
    return conn


def listing(conn, **filters):
    return json.loads(list_exchanges(conn, ExchangeQuery(**filters)))


def ids(conn, **filters):
    return [summary["id"] for summary in listing(conn, **filters)["exchanges"]]


def test_list_returns_projected_summaries_newest_first(populated):
    body = listing(populated)

    assert [summary["id"] for summary in body["exchanges"]] == ["8", "7", "6", "5", "4", "3", "2", "1"]
    assert body["next"] is None
    assert body["hidden"] == {}
    meter = body["exchanges"][-2]
    assert meter["headline"] == "1241.9 kWh"
    assert meter["not_sent"] == ["stats"]
    legs = {leg["peer"]: leg for leg in meter["legs"]}
    assert legs["primary"]["outcome"] == "delivered"
    assert legs["primary"]["rewritten"] == []
    assert legs["ha"]["rewritten"] == [{"field": "tx", "from": 10216, "to": 77}]


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"charger": "other"}, ["6"]),
        ({"action": "MeterValues"}, ["6", "2"]),
        ({"outcome": "answered_by_proxy"}, ["5", "4"]),
        ({"outcome": "dropped"}, ["7"]),
        ({"problem": True}, ["7"]),
        ({"peer": "ha"}, ["6", "2"]),
        ({"peer": "primary", "action": "StartTransaction"}, ["1"]),
        ({"peer": "charger"}, ["5", "4"]),
        ({"tx": 10216, "charger": CP}, ["7", "2", "1"]),
        ({"q": "ha2"}, ["2"]),  # wire id
        ({"q": "4F2A"}, ["1"]),  # masked idTag
        ({"q": "1021", "charger": CP}, ["7", "2", "1"]),  # transaction id
        ({"q": "5"}, ["5"]),  # exchange id
        ({"q": "%"}, []),  # LIKE wildcards are literal
        ({"exclude": ("Heartbeat", "MeterValues")}, ["7", "5", "4", "1"]),
        ({"since": 400.0}, ["8", "7", "6", "5"]),
    ],
)
def test_filters(populated, filters, expected):
    assert ids(populated, **filters) == expected


def test_hidden_heartbeats_are_counted_under_the_other_filters(populated):
    assert listing(populated, exclude=("Heartbeat",))["hidden"] == {"Heartbeat": 2}
    assert listing(populated, exclude=("Heartbeat",), since=300.0)["hidden"] == {"Heartbeat": 1}
    assert listing(populated, exclude=("Heartbeat",), limit=1)["hidden"] == {"Heartbeat": 2}


def test_keyset_paging_walks_every_exchange_once(populated):
    seen, cursor = [], None
    while True:
        body = listing(populated, limit=3, before=cursor)
        seen += [summary["id"] for summary in body["exchanges"]]
        cursor = body["next"]
        if cursor is None:
            break

    assert seen == ["8", "7", "6", "5", "4", "3", "2", "1"]


def test_limit_is_clamped(conn):
    for n in range(MAX_LIMIT + 5):
        store(conn, n + 1, heartbeat(float(n), n + 1))

    assert len(ids(conn, limit=10_000)) == MAX_LIMIT
    assert len(ids(conn, limit=0)) == 1


@pytest.mark.parametrize("cursor", ["", "abc", "1.0", "x:1", "1.0:2:3"])
def test_invalid_cursor(populated, cursor):
    with pytest.raises(InvalidCursor):
        listing(populated, before=cursor)


def test_exchange_ids_are_strings_even_when_huge(conn):
    big = 2**62 + 12345
    store(conn, big, heartbeat(1.0, big))

    body = list_exchanges(conn, ExchangeQuery())
    assert f'"id":"{big}"'.encode() in body
    assert json.loads(exchange_detail(conn, big))["id"] == str(big)


def test_detail_fills_in_frames_and_decisions(populated):
    detail = json.loads(exchange_detail(populated, 2))

    assert detail["payload"] == METER
    frames = detail["frames"]
    assert [(frame["peer"], frame["dir"], frame["msg_type"]) for frame in frames] == [
        ("charger", "in", "call"),
        ("primary", "out", "call"),
        ("ha", "out", "call"),
        ("primary", "in", "result"),
    ]
    assert frames[1]["payload"] == METER
    assert frames[1]["same_as_origin"] is True
    assert frames[2]["payload"]["transactionId"] == 77
    assert frames[2]["same_as_origin"] is False


def test_detail_of_errors_and_decisions(populated):
    refused_detail = json.loads(exchange_detail(populated, 5))
    error_frame = refused_detail["frames"][-1]
    assert error_frame["error"] == {"code": "NotSupported", "description": "no"}
    assert error_frame["payload"] == {}

    blocked_detail = json.loads(exchange_detail(populated, 4))
    assert blocked_detail["decisions"] == [
        {
            "at": 300.0,
            "peer": "ha",
            "reason": "POLICY_ANSWERED",
            "text": "Answered by the proxy for ha; the charger never saw it",
            "detail": None,
        }
    ]
    assert blocked_detail["legs"][0]["replied_by"] == "proxy"


def test_unknown_exchange_detail_is_none(populated):
    assert exchange_detail(populated, 999) is None


def test_queued_then_delivered_after_a_restart_continues_the_same_exchange(conn):
    call = Call("s1", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921})
    store(conn, 9, [received(10.0, "charger", call), decided(10.1, "primary", Reason.QUEUED)], not_sent=("stats",))
    # after the restart a fresh writer only sees the delivery
    store(conn, 9, [sent(400.0, "primary", call), received(400.2, "primary", CallResult("s1", {}))])

    detail = json.loads(exchange_detail(conn, 9))
    assert detail["origin"] == "charger"
    assert detail["started_at"] == 10.0
    assert detail["last_at"] == 400.2
    assert detail["not_sent"] == ["stats"]
    assert detail["outcome"] == "delivered"
    primary = detail["legs"][0]
    assert (primary["outcome"], primary["reason"], primary["sent_at"]) == ("delivered", "QUEUED", 400.0)
    assert len(detail["frames"]) == 3


def test_open_leg_of_a_stored_incomplete_exchange_is_unknown(conn):
    store(conn, 1, heartbeat(0.0, 1))
    db.write_batch(conn, WriteBatch(incomplete=[1]))

    summary = listing(conn)["exchanges"][0]
    assert summary["incomplete"] is True
    assert summary["legs"][0]["reason_text"] == "Unknown (monitor gap)"


def test_stale_open_leg_is_unknown_relative_to_now(conn):
    store(conn, 1, heartbeat(0.0, 1))

    fresh = json.loads(list_exchanges(conn, ExchangeQuery(), now=10.0))
    stale = json.loads(list_exchanges(conn, ExchangeQuery(), now=1000.0))
    assert fresh["exchanges"][0]["legs"][0]["reason_text"] is None
    assert stale["exchanges"][0]["legs"][0]["reason_text"] == "Unknown (monitor gap)"


def test_session_summary(populated):
    session = json.loads(session_summary(populated, 10216))

    assert session == {
        "transaction_id": 10216,
        "charger_id": CP,
        "connector_id": 1,
        "id_tag": "***4F2A",
        "started_at": 100.0,
        "stopped_at": 600.0,
        "energy_wh": 7421,
        "exchanges": 4,  # start, cp meter values, stop, and the other charger's meter values
        "problems": 1,
    }


def test_session_without_a_stop_measures_energy_from_the_latest_meter_values(conn):
    store(conn, 1, start(100.0))
    store(conn, 2, meter_values(200.0, 2))

    session = json.loads(session_summary(conn, 10216))
    assert (session["stopped_at"], session["energy_wh"]) == (None, 7421)


def test_unknown_session_is_none(populated):
    assert session_summary(populated, 1) is None


def test_links_report_merges_the_snapshot_with_history_and_gaps(conn):
    db.write_batch(
        conn,
        WriteBatch(
            links=[LinkRow(CP, "stats", 10.0, False), LinkRow(CP, "stats", 500.0, True)],
            gaps=[Gap(CP, 5.0, 6.0, 3), Gap(CP, 600.0, 601.0, 37)],
        ),
    )
    snapshot = {"chargers": [], "monitor": {"fanout_failures": 0, "dropped_batches": 0}}

    report = json.loads(links_report(conn, snapshot, since=100.0))

    assert report == {
        **snapshot,
        "history": [{"charger_id": CP, "peer": "stats", "at": 500.0, "up": True}],
        "gaps": [{"charger_id": CP, "from_at": 600.0, "to_at": 601.0, "dropped": 37}],
    }


def test_a_read_running_past_the_time_limit_is_interrupted(conn):
    for n in range(300):
        store(conn, n + 1, heartbeat(float(n), n + 1))
    jumping_clock = itertools.count(step=1.0).__next__

    with pytest.raises(sqlite3.OperationalError, match="interrupted"):
        list_exchanges(conn, ExchangeQuery(), clock=jumping_clock)

    # the handler is removed and the transaction closed: the next read just works
    assert len(listing(conn)["exchanges"]) == MAX_LIMIT
    assert not conn.in_transaction


async def test_reads_through_a_readonly_worker(tmp_path):
    path = tmp_path / "monitor.sqlite"
    writer = SqliteWorker(path, readonly=False)
    reader = SqliteWorker(path, readonly=True)
    try:
        await writer.run(db.prepare_writer)
        await writer.run(store, 1, heartbeat(1.0, 1))
        body = await reader.run(list_exchanges, ExchangeQuery())
    finally:
        await reader.close()
        await writer.close()

    assert isinstance(body, bytes)
    assert json.loads(body)["exchanges"][0]["action"] == "Heartbeat"
