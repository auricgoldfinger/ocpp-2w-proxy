import itertools
import json

import pytest
from monitor_events import (
    CP,
    METER,
    blocked,
    decided,
    heartbeat,
    meter_values,
    received,
    refused,
    start,
    stop,
    store,
)

from ocpp_2w_proxy.monitor import db, payload_blob, queries
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.queries import (
    MAX_LIMIT,
    ExchangeQuery,
    InvalidCursor,
    ReadTimeLimit,
    exchange_detail,
    links_report,
    list_exchanges,
    rebuild_projections,
    session_summary,
)
from ocpp_2w_proxy.monitor.rows import LinkRow, WriteBatch
from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.reasons import Reason


@pytest.fixture
def populated(monitor_conn):
    store(monitor_conn, 1, start(100.0))
    store(monitor_conn, 2, meter_values(200.0, 2), not_sent=("stats",))
    store(monitor_conn, 3, heartbeat(250.0, 3))
    store(monitor_conn, 4, blocked(300.0))
    store(monitor_conn, 5, refused(400.0))
    store(monitor_conn, 6, meter_values(500.0, 6, charger="other"))
    store(monitor_conn, 7, stop(600.0))
    store(monitor_conn, 8, heartbeat(650.0, 8))
    return monitor_conn


def listing(monitor_conn, **filters):
    return json.loads(list_exchanges(monitor_conn, ExchangeQuery(**filters)))


def ids(monitor_conn, **filters):
    return [summary["id"] for summary in listing(monitor_conn, **filters)["exchanges"]]


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


def test_limit_is_clamped(monitor_conn):
    for n in range(MAX_LIMIT + 5):
        store(monitor_conn, n + 1, heartbeat(float(n), n + 1))

    assert len(ids(monitor_conn, limit=10_000)) == MAX_LIMIT
    assert len(ids(monitor_conn, limit=0)) == 1


@pytest.mark.parametrize("cursor", ["", "abc", "1.0", "x:1", "1.0:2:3"])
def test_invalid_cursor(populated, cursor):
    with pytest.raises(InvalidCursor):
        listing(populated, before=cursor)


def test_exchange_ids_are_strings_even_when_huge(monitor_conn):
    big = 2**62 + 12345
    store(monitor_conn, big, heartbeat(1.0, big))

    body = list_exchanges(monitor_conn, ExchangeQuery())
    assert f'"id":"{big}"'.encode() in body
    assert json.loads(exchange_detail(monitor_conn, big))["id"] == str(big)


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


def test_rebuild_projections_of_stored_exchanges_skips_unknown_ids(monitor_conn):
    call = Call("s1", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921})
    store(
        monitor_conn, 9, [received(10.0, "charger", call), decided(10.1, "primary", Reason.QUEUED)], not_sent=("stats",)
    )
    store(monitor_conn, 10, heartbeat(20.0, 10))

    (projection,) = rebuild_projections(monitor_conn, [9, 404])

    assert projection.exchange_id == 9
    assert (projection.origin, projection.action, projection.payload) == ("charger", "StopTransaction", call.payload)
    assert projection.not_sent == ("stats",)
    assert projection.summary()["legs"][0]["reason"] == "QUEUED"
    assert rebuild_projections(monitor_conn, []) == []


def test_stale_open_leg_is_unknown_relative_to_now(monitor_conn):
    store(monitor_conn, 1, heartbeat(0.0, 1))

    fresh = json.loads(list_exchanges(monitor_conn, ExchangeQuery(), now=10.0))
    stale = json.loads(list_exchanges(monitor_conn, ExchangeQuery(), now=1000.0))
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


def test_session_without_a_stop_measures_energy_from_the_latest_meter_values(monitor_conn):
    store(monitor_conn, 1, start(100.0))
    store(monitor_conn, 2, meter_values(200.0, 2))

    session = json.loads(session_summary(monitor_conn, 10216))
    assert (session["stopped_at"], session["energy_wh"]) == (None, 7421)


def test_unknown_exchange_or_session_is_none(populated):
    assert exchange_detail(populated, 999) is None
    assert session_summary(populated, 1) is None


def test_links_report_merges_the_snapshot_with_history_and_gaps(monitor_conn):
    db.write_batch(
        monitor_conn,
        WriteBatch(
            links=[LinkRow(CP, "stats", 10.0, False), LinkRow(CP, "stats", 500.0, True, "boot replayed")],
            gaps=[Gap(CP, 5.0, 6.0, 3), Gap(CP, 600.0, 601.0, 37)],
        ),
    )
    snapshot = {"chargers": [], "monitor": {"fanout_failures": 0, "dropped_batches": 0}}

    report = json.loads(links_report(monitor_conn, snapshot, since=100.0))

    assert report == {
        **snapshot,
        "history": [{"charger_id": CP, "peer": "stats", "at": 500.0, "up": True, "note": "boot replayed"}],
        "gaps": [{"charger_id": CP, "from_at": 600.0, "to_at": 601.0, "dropped": 37}],
    }


def test_a_read_running_past_the_time_limit_is_interrupted(monitor_conn):
    for n in range(300):
        store(monitor_conn, n + 1, heartbeat(float(n), n + 1))
    jumping_clock = itertools.count(step=1.0).__next__

    with pytest.raises(ReadTimeLimit):
        list_exchanges(monitor_conn, ExchangeQuery(), clock=jumping_clock)

    # the handler is removed and the transaction closed: the next read just works
    assert len(listing(monitor_conn)["exchanges"]) == MAX_LIMIT
    assert not monitor_conn.in_transaction


@pytest.fixture
def check_every_step(monkeypatch):
    """Check the deadline on every SQLite step, so any statement run after the clock passes it is interrupted."""
    monkeypatch.setattr(queries, "_PROGRESS_STEPS", 1)


@pytest.mark.usefixtures("check_every_step")
def test_the_time_limit_spares_begin_and_rollback(populated):
    expired_after_start = itertools.chain([0.0], itertools.repeat(1e9)).__next__

    with pytest.raises(ReadTimeLimit):
        list_exchanges(populated, ExchangeQuery(), clock=expired_after_start)

    assert not populated.in_transaction
    assert len(listing(populated)["exchanges"]) == 8


def clock_expiring_on_call(monkeypatch, owner, name):
    """A clock that reads 0.0 until ``owner.name`` is first called, then far past any read deadline."""
    called = False
    original = getattr(owner, name)

    def calling(*args, **kwargs):
        nonlocal called
        called = True
        return original(*args, **kwargs)

    monkeypatch.setattr(owner, name, calling)
    return lambda: 1e9 if called else 0.0


# Decoding payloads and replaying them run outside the time limit: only SQL counts against it.


@pytest.mark.usefixtures("check_every_step")
@pytest.mark.parametrize(("owner", "name"), [(payload_blob, "decode"), (queries, "project")])
def test_listing_does_not_count_decoding_or_replay_against_the_time_limit(populated, monkeypatch, owner, name):
    query = ExchangeQuery(exclude=(queries.HEARTBEAT,))  # SQL after the fetch: a replay moved before it is caught
    expected = list_exchanges(populated, query)
    clock = clock_expiring_on_call(monkeypatch, owner, name)

    assert list_exchanges(populated, query, clock=clock) == expected


@pytest.mark.usefixtures("check_every_step")
def test_detail_does_not_count_decoding_against_the_time_limit(populated, monkeypatch):
    expected = exchange_detail(populated, 2)
    clock = clock_expiring_on_call(monkeypatch, payload_blob, "decode")

    assert exchange_detail(populated, 2, clock=clock) == expected


@pytest.mark.usefixtures("check_every_step")
def test_session_does_not_count_decoding_against_the_time_limit(populated, monkeypatch):
    clock = clock_expiring_on_call(monkeypatch, payload_blob, "decode")

    assert json.loads(session_summary(populated, 10216, clock=clock))["energy_wh"] == 7421
