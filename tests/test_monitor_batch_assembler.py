import itertools
import json

import pytest

from ocpp_2w_proxy.monitor.batch_assembler import BatchAssembler
from ocpp_2w_proxy.monitor.events import EventKind, FlowEvent
from ocpp_2w_proxy.monitor.gaps import Gap
from ocpp_2w_proxy.monitor.not_sent import NotSentMap
from ocpp_2w_proxy.monitor.projection import ExchangeProjection
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.reasons import Reason

CP = "cp"
METER = {"connectorId": 1, "transactionId": 10216, "meterValue": []}
NOT_SENT = NotSentMap({CP: {"MeterValues": ("stats",)}}, {CP: ("ha", "stats")})


def received(at, peer, message):
    return (EventKind.RECEIVED, at, CP, peer, message, None, None)


def sent(at, peer, message):
    return (EventKind.SENT, at, CP, peer, message, None, None)


def decided(at, peer, call, reason, detail=None):
    return (EventKind.DECIDED, at, CP, peer, call, reason, detail)


def link(at, peer, up, note=None):
    return (EventKind.LINK, at, CP, peer, None, None, (up, note))


def assembler():
    return BatchAssembler(NOT_SENT, new_exchange_id=itertools.count(900).__next__)


def run(events, stored=(), asm=None):
    """One batch, as the writer drives it (without slicing); ``stored`` plays the rebuild from the store."""
    asm = assembler() if asm is None else asm
    routed = [asm.correlate(raw) for raw in events]
    unknown = asm.take_unknown()
    asm.adopt([projection for projection in stored if projection.exchange_id in unknown], now=0.0)
    for item in routed:
        asm.fold(item)
    summaries = {summary["id"]: summary for summary in map(asm.summarize, asm.changed())}
    return asm.take_batch(), summaries, unknown


def payloads(batch):
    return [(f.peer, f.dir, None if f.payload is None else json.loads(f.payload)) for f in batch.frames]


def test_forwarded_exchange_stores_only_the_payloads_that_differ_from_the_origin():
    origin = Call("w1", "MeterValues", METER)
    same = origin.with_id("p1")  # the very same payload object
    equal = Call("h1", "MeterValues", dict(METER), exchange_id=origin.exchange_id)
    rewritten = Call("s1", "MeterValues", {**METER, "transactionId": 77}, exchange_id=origin.exchange_id)

    batch, summaries, _ = run(
        [
            received(1.0, "charger", origin),
            sent(1.1, "primary", same),
            sent(1.2, "ha", equal),
            sent(1.3, "extra", rewritten),
            received(1.4, "primary", CallResult("p1", {})),
            sent(1.5, "charger", CallResult("w1", {})),
        ],
    )

    assert payloads(batch) == [
        ("charger", "in", METER),
        ("primary", "out", None),
        ("ha", "out", None),
        ("extra", "out", {**METER, "transactionId": 77}),
        ("primary", "in", {}),
        ("charger", "out", {}),
    ]
    assert {f.exchange_id for f in batch.frames} == {origin.exchange_id}
    (row,) = batch.exchanges
    assert (row.origin, row.action, row.not_sent) == ("charger", "MeterValues", ("stats",))
    summary = summaries[str(origin.exchange_id)]
    assert summary["not_sent"] == ["stats"]
    assert {leg["peer"]: leg["outcome"] for leg in summary["legs"]} == {
        "primary": "delivered",
        "ha": None,
        "extra": None,
    }


def test_an_orphan_reply_is_redacted_as_unknown_so_redaction_fails_closed():
    batch, summaries, unknown = run([received(1.0, "primary", CallResult("zz", {"idTag": "04A1B2C3D4"}))])

    (frame,) = batch.frames
    assert frame.exchange_id == 900
    assert json.loads(frame.payload) == {"idTag": "***C3D4"}
    assert summaries["900"]["origin"] is None
    assert unknown == set()


def test_call_error_details_are_always_redacted():
    call = Call("w1", "Heartbeat", {})

    batch, _, _ = run(
        [
            received(1.0, "charger", call),
            sent(1.1, "primary", call),
            received(1.2, "primary", CallError("w1", "InternalError", "boom", {"idTag": "04A1B2C3D4"})),
        ],
    )

    assert json.loads(batch.frames[-1].payload)["details"] == {"idTag": "***C3D4"}


def test_the_origin_payload_is_redacted_but_the_relayed_object_is_untouched():
    start = {"connectorId": 1, "idTag": "04A1B2C3D4", "meterStart": 0, "timestamp": "t"}
    call = Call("t1", "StartTransaction", start)

    batch, summaries, _ = run([received(1.0, "charger", call), sent(1.1, "primary", call)])

    assert payloads(batch) == [("charger", "in", {**start, "idTag": "***C3D4"}), ("primary", "out", None)]
    assert summaries[str(call.exchange_id)]["id_tag"] == "***C3D4"
    assert start["idTag"] == "04A1B2C3D4"


def test_not_sent_only_for_exchanges_from_the_charger():
    from_charger = Call("w1", "MeterValues", METER)
    from_primary = Call("r1", "MeterValues", METER)  # not a real command, but the same action

    batch, _, _ = run([received(1.0, "charger", from_charger), received(1.0, "primary", from_primary)])

    assert {row.exchange_id: row.not_sent for row in batch.exchanges} == {
        from_charger.exchange_id: ("stats",),
        from_primary.exchange_id: (),
    }


def test_a_continued_exchange_not_in_memory_is_reported_for_a_rebuild():
    queued = Call("s1", "StopTransaction", {"transactionId": 1})
    fresh = Call("w1", "Heartbeat", {})

    _, _, unknown = run(
        [
            sent(5.0, "primary", queued),
            received(5.1, "charger", fresh),
            sent(5.2, "primary", fresh),
        ],
    )

    assert unknown == {queued.exchange_id}


def test_an_exchange_rebuilt_from_the_store_continues_with_its_stored_origin():
    queued = Call("s1", "StopTransaction", {"transactionId": 1, "idTag": "04A1B2C3D4"})
    stored = ExchangeProjection(queued.exchange_id)
    stored.known_origin = "charger"
    stored_call = Call("s1", "StopTransaction", {"transactionId": 1, "idTag": "***C3D4"})
    stored.apply(FlowEvent(EventKind.RECEIVED, 1.0, CP, "charger", stored_call, None, None))
    stored.apply(FlowEvent(EventKind.DECIDED, 1.1, CP, "primary", None, Reason.QUEUED, None))

    batch, summaries, unknown = run(
        [sent(5.0, "primary", queued), received(5.2, "primary", CallResult("s1", {}))], stored=[stored]
    )

    assert unknown == {queued.exchange_id}
    assert payloads(batch) == [("primary", "out", None), ("primary", "in", {})]  # equal to the stored origin
    summary = summaries[str(queued.exchange_id)]
    assert (summary["origin"], summary["started_at"], summary["outcome"]) == ("charger", 1.0, "delivered")


def test_link_down_is_stored_and_turns_a_pending_reply_into_an_orphan():
    command = Call("r1", "Reset", {"type": "Soft"})

    batch, summaries, _ = run(
        [
            received(1.0, "primary", command),
            sent(1.1, "charger", command.with_id("c1")),
            link(2.0, "charger", False, "dropped without close frame"),
            link(3.0, "charger", True),
            received(4.0, "charger", CallResult("c1", {"status": "Accepted"})),
        ],
    )

    assert [(row.peer, row.up, row.note) for row in batch.links] == [
        ("charger", False, "dropped without close frame"),
        ("charger", True, None),
    ]
    assert summaries["900"]["origin"] is None
    (leg,) = summaries[str(command.exchange_id)]["legs"]
    assert (leg["peer"], leg["reply"]) == ("charger", None)


def test_a_reply_after_charger_no_reply_attaches_late_and_a_second_one_as_duplicate():
    command = Call("r1", "Reset", {"type": "Soft"})
    to_charger = command.with_id("c1")

    _, summaries, _ = run(
        [
            received(1.0, "primary", command),
            sent(1.1, "charger", to_charger),
            decided(31.1, "charger", to_charger, Reason.CHARGER_NO_REPLY),
            received(40.0, "charger", CallResult("c1", {"status": "Accepted"})),
            received(41.0, "charger", CallResult("c1", {"status": "Accepted"})),
        ],
    )

    (summary,) = summaries.values()
    (leg,) = summary["legs"]
    assert (leg["peer"], leg["outcome"], leg["late"], leg["duplicate"]) == ("charger", "failed", True, True)


GAP = Gap(CP, 10.0, 12.0, 37)


def test_a_gap_marks_the_exchanges_open_in_it_incomplete():
    open_call = Call("w1", "MeterValues", METER)
    done_call = Call("w2", "Heartbeat", {})
    asm = assembler()
    run(
        [
            received(9.0, "charger", open_call),
            sent(9.1, "primary", open_call),
            received(5.0, "charger", done_call),
            sent(5.1, "primary", done_call),
            received(5.2, "primary", CallResult("w2", {})),
        ],
        asm=asm,
    )

    asm.record_gaps([GAP], now=13.0)
    asm.mark_incomplete([GAP], asm.tracked())
    summaries = [asm.summarize(tracked) for tracked in asm.changed()]
    batch = asm.take_batch()

    assert [s["id"] for s in summaries] == [str(open_call.exchange_id)]
    assert summaries[0]["incomplete"] is True
    assert [row.incomplete for row in batch.exchanges] == [True]


def test_gaps_are_stored_and_a_later_orphan_in_the_reply_window_is_incomplete():
    asm = assembler()
    asm.record_gaps([GAP], now=13.0)
    assert asm.take_batch().gaps == [GAP]

    _, summaries, _ = run(
        [
            received(30.0, "primary", CallResult("zz", {})),
            received(12.0 + 121.0, "primary", CallResult("yy", {})),
        ],
        asm=asm,
    )

    assert [s["incomplete"] for s in summaries.values()] == [True, False]


@pytest.mark.parametrize("peer", ["primary", "charger"])
def test_a_proxy_reply_to_a_received_call_joins_its_exchange(peer):
    call = Call("w1", "Heartbeat", {})

    batch, summaries, _ = run(
        [
            received(1.0, peer, call),
            decided(1.1, peer, call, Reason.POLICY_ANSWERED),
            sent(1.2, peer, CallResult("w1", {})),
        ],
    )

    assert {f.exchange_id for f in batch.frames} == {call.exchange_id}
    assert [d.reason for d in batch.decisions] == ["POLICY_ANSWERED"]
    assert list(summaries) == [str(call.exchange_id)]


def test_record_stores_the_same_exchange_row_as_summarize():
    call = Call("w1", "MeterValues", METER)
    events = [
        received(1.0, "charger", call),
        sent(1.1, "primary", call),
        received(1.2, "primary", CallResult("w1", {})),
    ]
    recorded = assembler()

    summarized_batch, _, _ = run(events)
    for item in [recorded.correlate(raw) for raw in events]:
        recorded.fold(item)
    for tracked in recorded.changed():
        recorded.record(tracked)

    assert recorded.take_batch().exchanges == summarized_batch.exchanges
