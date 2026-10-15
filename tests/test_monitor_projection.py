import pytest

from ocpp_2w_proxy.monitor.correlator import Correlator
from ocpp_2w_proxy.monitor.events import EventKind, FlowEvent
from ocpp_2w_proxy.monitor.legs import UNKNOWN_TEXT
from ocpp_2w_proxy.monitor.projection import ExchangeProjection
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason

CP = "cp"
METER = {
    "connectorId": 1,
    "transactionId": 10216,
    "meterValue": [{"sampledValue": [{"value": "1234500", "unit": "Wh"}]}],
}
MV = Call("w1", "MeterValues", METER)
MV_HA = Call("x1", "MeterValues", {**METER, "transactionId": 1791822333})
MV_HA_EQUAL = Call("x1", "MeterValues", dict(METER))
STOP = Call("s1", "StopTransaction", {"transactionId": 10216, "meterStop": 1241921, "reason": "Local"})
START = Call("t1", "StartTransaction", {"connectorId": 1, "idTag": "***4F2A", "meterStart": 0})
PROFILE = Call("p1", "SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {"transactionId": 77}})
REMOTE_STOP = Call("r1", "RemoteStopTransaction", {"transactionId": 5})
REMOTE_STOP_CHARGER = Call("c1", "RemoteStopTransaction", {"transactionId": 10216})
HEARTBEAT = Call("h1", "Heartbeat", {})
OK = CallResult("w1", {})


def received(at, peer, message):
    return FlowEvent(EventKind.RECEIVED, at, CP, peer, message, None, None)


def sent(at, peer, message):
    return FlowEvent(EventKind.SENT, at, CP, peer, message, None, None)


def decided(at, peer, reason, detail=None):
    return FlowEvent(EventKind.DECIDED, at, CP, peer, None, reason, detail)


def link(at, peer, up):
    return FlowEvent(EventKind.LINK, at, CP, peer, None, None, up)


def project(events, exchange_id=1, **attributes):
    projection = ExchangeProjection(exchange_id)
    for name, value in attributes.items():
        setattr(projection, name, value)
    for event in events:
        projection.apply(event)
    return projection


def legs_by_peer(summary):
    return {leg["peer"]: leg for leg in summary["legs"]}


def assert_subset(actual, expected):
    assert {key: actual[key] for key in expected} == expected


SCENARIOS = {
    "forwarded": (
        [
            received(0.0, "charger", MV),
            sent(0.1, "primary", MV),
            sent(0.2, "ha", MV_HA),
            received(0.3, "primary", OK),
            sent(0.4, "charger", OK),
            received(0.5, "ha", CallResult("x1", {})),
        ],
        {"origin": "charger", "action": "MeterValues", "outcome": "delivered", "problem": False},
        {
            "primary": {"outcome": "delivered", "reply": "result", "replied_by": "peer", "rewritten": []},
            "ha": {
                "wire_id": "x1",
                "sent_at": 0.2,
                "replied_at": 0.5,
                "rewritten": [{"field": "tx", "from": 10216, "to": 1791822333}],
            },
        },
    ),
    "equal copy gives no chip": (
        [received(0.0, "charger", MV), sent(0.1, "ha", MV_HA_EQUAL)],
        {"outcome": None},
        {"ha": {"rewritten": [], "outcome": None, "reply": None, "replied_by": None}},
    ),
    "queued": (
        [received(0.0, "charger", STOP), decided(0.1, "primary", Reason.QUEUED), sent(0.1, "charger", OK)],
        {"outcome": "queued", "problem": False},
        {
            "primary": {
                "outcome": "queued",
                "reason": "QUEUED",
                "reason_text": "Queued for primary during an outage; acknowledged locally",
                "reply": None,
                "replied_by": None,
            }
        },
    ),
    "blocked (proxy CallResult)": (
        [
            received(0.0, "ha", PROFILE),
            decided(0.1, "ha", Reason.POLICY_ANSWERED),
            sent(0.1, "ha", CallResult("p1", {"status": "Accepted"})),
        ],
        {"origin": "ha", "outcome": "answered_by_proxy", "problem": False},
        {
            "charger": {
                "outcome": "answered_by_proxy",
                "reason": "POLICY_ANSWERED",
                "reason_text": "Answered by the proxy for ha; the charger never saw it",
                "reply": "result",
                "replied_by": "proxy",
                "replied_at": 0.1,
                "wire_id": None,
            }
        },
    ),
    "refused (proxy CallError)": (
        [
            received(0.0, "ha", PROFILE),
            sent(0.1, "ha", CallError("p1", "NotSupported", "not allowed")),
            decided(0.1, "ha", Reason.POLICY_REFUSED),
        ],
        {"outcome": "answered_by_proxy", "problem": False},
        {"charger": {"reply": "error", "error_code": "NotSupported", "replied_by": "proxy"}},
    ),
    "withheld": (
        [
            received(0.0, "charger", START),
            decided(0.1, "primary", Reason.WITHHELD_UNAUTHORIZED),
            sent(0.1, "ha", START),
            received(0.2, "ha", CallResult("t1", {"transactionId": 3})),
        ],
        {"outcome": "withheld", "problem": False, "id_tag": "***4F2A", "connector_id": 1, "transaction_id": None},
        {"primary": {"outcome": "withheld", "reason": "WITHHELD_UNAUTHORIZED"}, "ha": {"outcome": "delivered"}},
    ),
    "start takes its tx from the primary reply": (
        [
            received(0.0, "charger", START),
            sent(0.1, "primary", START),
            received(0.2, "primary", CallResult("t1", {"transactionId": 10216})),
        ],
        {"transaction_id": 10216, "headline": "0.0 kWh"},
        {},
    ),
    "late reply": (
        [
            received(0.0, "primary", REMOTE_STOP),
            sent(0.1, "charger", REMOTE_STOP_CHARGER),
            decided(30.1, "primary", Reason.CHARGER_NO_REPLY),
            received(40.0, "charger", CallResult("c1", {"status": "Accepted"})),
        ],
        {"origin": "primary", "outcome": "failed", "problem": True, "transaction_id": 10216},
        {"charger": {"outcome": "failed", "reason": "CHARGER_NO_REPLY", "late": True, "reply": None}},
    ),
    "duplicate reply": (
        [
            received(0.0, "charger", MV),
            sent(0.1, "primary", MV),
            received(0.2, "primary", OK),
            received(0.3, "primary", OK),
        ],
        {"outcome": "delivered"},
        {"primary": {"duplicate": True, "late": False, "replied_at": 0.2}},
    ),
    "retry under a new wire id": (
        [
            received(0.0, "charger", MV),
            sent(0.1, "primary", MV),
            sent(5.0, "primary", MV.with_id("w2")),
            received(5.1, "primary", CallResult("w2", {})),
        ],
        {"outcome": "delivered"},
        {"primary": {"wire_id": "w2", "sent_at": 5.0, "duplicate": False}},
    ),
    "peer CallError": (
        [
            received(0.0, "charger", MV),
            sent(0.1, "primary", MV),
            received(0.2, "primary", CallError("w1", "InternalError")),
        ],
        {"outcome": "failed", "problem": True},
        {"primary": {"outcome": "failed", "reply": "error", "error_code": "InternalError", "replied_by": "peer"}},
    ),
    "superseded is no problem": (
        [received(0.0, "charger", MV), decided(0.1, "primary", Reason.SUPERSEDED)],
        {"outcome": "dropped", "problem": False},
        {},
    ),
    "overflow is a problem": (
        [received(0.0, "charger", MV), decided(0.1, "primary", Reason.QUEUE_OVERFLOW)],
        {"outcome": "dropped", "problem": True},
        {},
    ),
    "heartbeat answered locally": (
        [
            received(0.0, "charger", HEARTBEAT),
            decided(0.0, "primary", Reason.HEARTBEAT_LOCAL),
            sent(0.0, "charger", CallResult("h1", {"currentTime": "now"})),
        ],
        {"outcome": "answered_by_proxy"},
        {"primary": {"replied_by": "proxy", "reply": "result"}},
    ),
    "debug command": (
        [
            decided(0.0, "charger", Reason.DEBUG_COMMAND),
            sent(0.0, "charger", REMOTE_STOP_CHARGER),
            received(0.1, "charger", CallResult("c1", {})),
        ],
        {"origin": "debug", "outcome": "delivered"},
        {"charger": {"reason": "DEBUG_COMMAND", "replied_by": "peer"}},
    ),
    "proxy originated": (
        [sent(0.0, "primary", HEARTBEAT)],
        {"origin": "proxy", "proxy_kind": None, "outcome": None},
        {"primary": {"outcome": None, "reason_text": None}},
    ),
    "orphan reply": (
        [received(0.0, "primary", CallResult("zz", {}))],
        {"origin": None, "action": None, "headline": None},
        {"primary": {"reply": "result", "sent_at": None}},
    ),
    "link events belong to no exchange": (
        [link(0.0, "primary", False)],
        {"charger_id": None, "started_at": None, "legs": []},
        {},
    ),
}


@pytest.mark.parametrize(("events", "expected", "expected_legs"), SCENARIOS.values(), ids=SCENARIOS)
def test_projection(events, expected, expected_legs):
    summary = project(events).summary()

    assert_subset(summary, expected)
    legs = legs_by_peer(summary)
    for peer, expected_leg in expected_legs.items():
        assert_subset(legs[peer], expected_leg)


def test_summary_carries_the_scalar_fields():
    summary = project([received(1.0, "charger", MV), sent(1.5, "primary", MV)], exchange_id=2**62 + 1).summary()

    assert_subset(
        summary,
        {
            "id": str(2**62 + 1),
            "charger_id": CP,
            "started_at": 1.0,
            "last_at": 1.5,
            "incomplete": False,
            "transaction_id": 10216,
            "connector_id": 1,
            "id_tag": None,
            "headline": "1234.5 kWh",
            "not_sent": [],
        },
    )
    assert "proxy_kind" not in summary


def test_queued_then_delivered_after_a_restart_with_the_same_exchange_id():
    before = project([received(0.0, "charger", STOP), decided(0.1, "primary", Reason.QUEUED)], exchange_id=9)
    assert before.summary()["outcome"] == "queued"

    after = project([sent(300.0, "primary", STOP), received(300.2, "primary", CallResult("s1", {}))], exchange_id=9)
    resumed = project(
        [
            received(0.0, "charger", STOP),
            decided(0.1, "primary", Reason.QUEUED),
            sent(300.0, "primary", STOP),
            received(300.2, "primary", CallResult("s1", {})),
        ],
        exchange_id=9,
    )

    for summary in (after.summary(), resumed.summary()):
        assert summary["id"] == "9"
        assert summary["outcome"] == "delivered"
        assert summary["last_at"] == 300.2
    assert_subset(legs_by_peer(resumed.summary())["primary"], {"outcome": "delivered", "reason": "QUEUED"})
    assert resumed.summary()["origin"] == "charger"


def test_not_sent_never_lists_a_peer_with_a_leg():
    projection = project([received(0.0, "charger", MV), sent(0.1, "ha", MV)], not_sent=("stats", "ha"))

    assert projection.summary()["not_sent"] == ["stats"]


def test_open_leg_of_an_incomplete_exchange_is_unknown():
    summary = project([received(0.0, "charger", MV), sent(0.1, "primary", MV)], incomplete=True).summary()

    assert summary["incomplete"] is True
    assert summary["problem"] is False
    assert_subset(legs_by_peer(summary)["primary"], {"outcome": None, "reason_text": UNKNOWN_TEXT})


@pytest.mark.parametrize(("now", "expected"), [(None, None), (100.0, None), (200.0, UNKNOWN_TEXT)])
def test_leg_open_beyond_the_stale_limit_is_unknown(now, expected):
    projection = project([received(0.0, "charger", MV), sent(0.1, "primary", MV)])

    assert legs_by_peer(projection.summary(now))["primary"]["reason_text"] == expected


def test_closed_legs_are_never_unknown():
    projection = project(SCENARIOS["forwarded"][0], incomplete=True)

    assert all(leg["reason_text"] is None for leg in projection.summary(now=1e9)["legs"])


class Pipeline:
    """Correlates like the writer: Calls carry their exchange id, replies are resolved by the correlator."""

    def __init__(self):
        self.correlator = Correlator()
        self.projections: dict[int, ExchangeProjection] = {}
        self._orphan_ids = iter(range(1000, 2000))

    def feed(self, event, exchange_id=None):
        if event.kind is EventKind.LINK:
            if not event.detail:
                self.correlator.link_down(event.charger_id, event.peer)
            return
        if event.kind is EventKind.SENT and isinstance(event.message, Call):
            self.correlator.register_sent(event.charger_id, event.peer, event.message.id, exchange_id, event.at)
        if event.kind is EventKind.RECEIVED and not isinstance(event.message, Call):
            resolved = self.correlator.resolve_reply(event.charger_id, event.peer, event.message.id, event.at)
            exchange_id = next(self._orphan_ids) if resolved is None else resolved[0]
        self.projections.setdefault(exchange_id, ExchangeProjection(exchange_id)).apply(event)


def test_link_down_turns_a_pending_reply_into_an_orphan():
    pipeline = Pipeline()
    pipeline.feed(received(0.0, "charger", MV), exchange_id=1)
    pipeline.feed(sent(0.1, "primary", MV), exchange_id=1)
    pipeline.feed(link(0.2, "primary", False))
    pipeline.feed(received(0.3, "primary", OK))

    assert legs_by_peer(pipeline.projections[1].summary())["primary"]["reply"] is None
    assert pipeline.projections[1000].summary()["origin"] is None


def test_pipeline_attaches_late_and_duplicate_replies_to_the_command():
    pipeline = Pipeline()
    pipeline.feed(received(0.0, "primary", REMOTE_STOP), exchange_id=1)
    pipeline.feed(sent(0.1, "charger", REMOTE_STOP_CHARGER), exchange_id=1)
    pipeline.feed(decided(30.1, "primary", Reason.CHARGER_NO_REPLY), exchange_id=1)
    pipeline.correlator.mark_timed_out(CP, "charger", "c1")
    pipeline.feed(received(40.0, "charger", CallResult("c1", {})))
    pipeline.feed(received(41.0, "charger", CallResult("c1", {})))

    assert list(pipeline.projections) == [1]
    assert_subset(legs_by_peer(pipeline.projections[1].summary())["charger"], {"late": True, "duplicate": True})
