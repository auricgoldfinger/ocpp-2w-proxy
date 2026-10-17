from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest
from conftest import CHARGER_ID, make_raw_config
from fakes import Decision, FakeCsms, RecordingFlow, dead_port, eventually, responding

from ocpp_2w_proxy import backoff
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.primary_channel import PrimaryChannel, PrimaryUnavailable
from ocpp_2w_proxy.reasons import Reason
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog

START = {"connectorId": 1, "idTag": "tag", "meterStart": 0, "timestamp": "2026-01-01T00:00:00Z"}
BOOT = {"chargePointVendor": "Grubby"}


async def _ignore(call: Call) -> None:
    pass


@pytest.fixture
async def channels(tmp_path):
    """Builds PrimaryChannels for the test charger (an unreachable primary unless a url is given)
    and closes every one of them after the test."""
    made = []

    def build(url="ws://127.0.0.1:1", flow=None, store=None, **primary_config) -> PrimaryChannel:
        config = parse(make_raw_config(url, None, tmp_path, primary={"auth": "none"}), {})
        channel = PrimaryChannel(
            replace(config.primary, **primary_config),
            config.chargers[CHARGER_ID],
            store or StateStore.for_charger(tmp_path, CHARGER_ID),
            flow or TrafficLog(CHARGER_ID, False),
        )
        made.append(channel)
        return channel

    yield build
    for channel in made:
        await channel.close()


@pytest.fixture
def attached(channels, primary):
    """Builds a channel to the fake primary with a session attached to it."""

    async def build(flow=None, on_call=_ignore, **primary_config) -> PrimaryChannel:
        channel = channels(primary.url, flow, **primary_config)
        await channel.attach(channel._identity, on_call)
        return channel

    return build


def _cache_boot(tmp_path) -> None:
    store = StateStore.for_charger(tmp_path, CHARGER_ID)
    store.state.boot = BOOT
    store.save()


async def test_primary_queue_overflow_drops_meter_values_first(channels, tmp_path):
    store = StateStore.for_charger(tmp_path, CHARGER_ID)
    # Below the configurable minimum, so overflow is reached with a handful of calls.
    channel = channels(store=store, max_queue=2)
    channel._unavailable(Call("m0", "MeterValues", {"connectorId": 1, "transactionId": 1}))
    channel._unavailable(Call("st1", "StopTransaction", {"transactionId": 1}))
    channel._unavailable(Call("m1", "MeterValues", {"connectorId": 1, "transactionId": 2}))
    channel._unavailable(Call("st2", "StopTransaction", {"transactionId": 2}))

    # The meter readings are sacrificed so the billing-critical stops survive.
    assert [call.id for call in channel._queue] == ["st1", "st2"]
    assert [call["id"] for call in store.state.primary_outbox] == ["st1", "st2"]


async def test_primary_queue_overflow_drops_oldest_when_no_meter_values(channels):
    flow = RecordingFlow()
    channel = channels(flow=flow, max_queue=2)
    for message_id in ("one", "two", "three"):
        channel._unavailable(Call(message_id, "StopTransaction", {"transactionId": 1}))

    assert [call.id for call in channel._queue] == ["two", "three"]
    # The dropped call is reported after it was queued.
    assert [(d.call.id, d.reason) for d in flow.decisions] == [
        ("one", Reason.QUEUED),
        ("two", Reason.QUEUED),
        ("three", Reason.QUEUED),
        ("one", Reason.QUEUE_OVERFLOW),
    ]


async def test_outage_keeps_only_the_newest_status_and_drops_stale_readings(channels, tmp_path):
    flow = RecordingFlow()
    store = StateStore.for_charger(tmp_path, CHARGER_ID)
    channel = channels(flow=flow, store=store)
    older = Call("a", "StatusNotification", {"connectorId": 1, "status": "Preparing"})
    newer = Call("b", "StatusNotification", {"connectorId": 1, "status": "Charging"})
    other = Call("c", "StatusNotification", {"connectorId": 2, "status": "Available"})
    for call in (older, newer, other):
        channel._unavailable(call)
    reply = channel._unavailable(Call("m", "MeterValues", {"connectorId": 1}))

    assert reply == CallResult("m", {})
    assert not channel._queue
    assert channel.queued_count == 2
    assert sorted(call.id for call in channel._latest.values()) == ["b", "c"]
    assert sorted(item["id"] for item in store.state.primary_latest.values()) == ["b", "c"]
    assert [(d.call.id, d.reason) for d in flow.decisions] == [
        ("a", Reason.KEPT_LATEST),
        ("a", Reason.SUPERSEDED),
        ("b", Reason.KEPT_LATEST),
        ("c", Reason.KEPT_LATEST),
        ("m", Reason.DROPPED_UNAVAILABLE),
    ]


async def test_live_calls_get_no_substitute_answer_while_the_primary_is_down(channels):
    channel = channels()
    for action in ("Authorize", "StartTransaction", "DataTransfer"):
        with pytest.raises(PrimaryUnavailable):
            await channel.call(Call("x", action, {}), timeout=0.1)
    assert not channel._queue
    assert not channel._latest


async def test_live_queued_actions_wait_behind_the_outbox(primary, attached):
    """A StatusNotification sent live while an older queued stop is still unconfirmed
    would overtake it on the primary: keep it behind the stop instead."""
    primary.responder = responding(StopTransaction=None)
    channel = await attached()
    channel._queue.append(Call("old", "StopTransaction", {"transactionId": 1}))  # left over from an outage

    status = await channel.call(Call("new", "StatusNotification", {"connectorId": 1, "status": "Available"}), timeout=3)
    assert status == CallResult("new", {})  # acknowledged locally, not sent out of order
    assert [call.id for call in channel._latest.values()] == ["new"]

    # Actions that are never queued still go out live.
    heartbeat = await channel.call(Call("hb", "Heartbeat", {}), timeout=3)
    assert heartbeat.payload["currentTime"] == "2026-01-01T00:00:00Z"
    await primary.wait_for_call("StopTransaction")
    await primary.wait_for_call("Heartbeat")
    assert "StatusNotification" not in primary.actions()


async def test_overflow_never_removes_the_call_being_sent(channels):
    """The drain holds the queue head while awaiting the primary's reply; an
    overflow then must drop another call, or the drain's popleft() afterwards
    would discard an unsent message."""
    # Below the configurable minimum, so overflow is reached with a handful of calls.
    channel = channels(max_queue=2)
    calls = [Call(i, "MeterValues", {"connectorId": 1, "transactionId": 1}) for i in ("a", "b", "c")]
    channel._queue.extend(calls)
    channel._in_flight = calls[0]  # the drain is waiting for its reply right now

    channel._enforce_queue_limit()

    assert channel._queue[0] is calls[0]  # the in-flight head survives
    assert calls[1] not in channel._queue  # the oldest droppable meter went instead
    assert len(channel._queue) == 2


async def test_reconnect_replays_the_cached_boot(primary, attached):
    """Backends that expect a BootNotification on every new connection must get
    one when the worker reconnects mid-session."""
    channel = await attached()
    boot = await channel.call(Call("b", "BootNotification", BOOT), timeout=3)
    assert boot.payload["status"] == "Accepted"
    await primary.wait_for_call("BootNotification")  # the charger's own boot

    await primary.drop_connection()  # the primary connection drops; the worker reconnects

    boots = await primary.wait_for_call("BootNotification", count=2)
    assert boots[-1]["chargePointVendor"] == "Grubby"  # the cached boot was replayed


async def test_stale_detach_cannot_unhook_the_newer_session(channels, primary):
    """UC-002 BR-004: a replaced session that outlives the replacement grace still runs
    detach() during its cleanup; that must not unhook the newer session's routing."""
    channel = channels(primary.url)
    routed = []

    async def on_call_a(call: Call) -> None:
        routed.append("a")

    async def on_call_b(call: Call) -> None:
        routed.append("b")

    token_a = await channel.attach(channel._identity, on_call_a)
    token_b = await channel.attach(channel._identity, on_call_b)

    channel.detach(token_a)  # the stale session's cleanup, after the grace expired
    await channel._dispatch_call(Call("stale", "Heartbeat", {}))
    assert routed == ["b"]

    channel.detach(token_b)  # the current session ends normally
    await channel._dispatch_call(Call("after", "Heartbeat", {}))
    assert routed == ["b"]


async def test_attach_during_backoff_sleep_is_served_and_drained(channels, tmp_path, monkeypatch):
    """A session attaching while the worker waits out a long retry is served at once.

    The link attach() returns must have a live reader, and the worker must wake from its
    backoff to drain the outbox; otherwise calls time out against a healthy primary.
    """
    monkeypatch.setattr(backoff, "MIN_RETRY_DELAY", 30.0)
    monkeypatch.setattr(backoff, "MAX_RETRY_DELAY", 60.0)
    port = await dead_port()  # the worker's first connect attempt fails; it then sleeps ~30s

    store = StateStore.for_charger(tmp_path, CHARGER_ID)
    store.state.primary_outbox.append({"id": "q1", "action": "MeterValues", "payload": {"connectorId": 1}})
    store.save()
    channel = channels(f"ws://127.0.0.1:{port}/ocpp", store=store)
    channel.start_background()
    await asyncio.sleep(0.05)  # let the worker reach its backoff sleep

    recovered = await FakeCsms().start(port)
    try:
        await channel.attach(channel._identity, _ignore)
        reply = await asyncio.wait_for(channel.call(Call("hb", "Heartbeat", {}), timeout=3), 5)
        assert isinstance(reply, CallResult)
        await asyncio.wait_for(recovered.wait_for_call("MeterValues"), 5)
    finally:
        await channel.close()
        await recovered.stop()


async def test_start_transaction_waits_for_the_queued_stop_of_the_previous_one(primary, attached):
    channel = await attached()
    channel._unavailable(Call("old", "StopTransaction", {"transactionId": 1, "meterStop": 5}))

    reply = await channel.call(Call("new", "StartTransaction", START), timeout=3)

    assert isinstance(reply, CallResult)
    assert primary.actions() == ["StopTransaction", "StartTransaction"]


async def test_start_transaction_is_refused_while_the_queue_does_not_drain(primary, attached):
    primary.responder = responding(StopTransaction=None)
    flow = RecordingFlow()
    channel = await attached(flow)
    channel._unavailable(Call("old", "StopTransaction", {"transactionId": 1, "meterStop": 5}))
    start = Call("new", "StartTransaction", START)

    reply = await channel.call(start, timeout=0.3)

    assert isinstance(reply, CallError)
    assert "StartTransaction" not in primary.actions()
    assert flow.decisions[-1] == Decision("primary", start, Reason.START_REFUSED_CATCHING_UP)


async def test_nothing_reaches_the_primary_before_the_replayed_boot_is_answered(primary, attached, tmp_path):
    primary.responder = responding(BootNotification=None)
    _cache_boot(tmp_path)
    channel = await attached()
    await primary.wait_for_call("BootNotification")

    reply = await channel.call(Call("hb", "Heartbeat", {}), timeout=0.3)

    assert isinstance(reply, CallResult)  # answered locally
    assert "Heartbeat" not in primary.actions()


async def test_the_chargers_identical_boot_is_not_sent_twice(primary, attached, tmp_path):
    _cache_boot(tmp_path)
    flow = RecordingFlow()
    channel = await attached(flow)
    call = Call("b1", "BootNotification", BOOT)

    first = await channel.call(call, timeout=3)
    assert first.id == "b1"
    assert first.payload["status"] == "Accepted"
    assert primary.actions().count("BootNotification") == 1  # the replay answered for it
    assert flow.decisions == [Decision("primary", call, Reason.BOOT_FROM_REPLAY)]

    await channel.call(Call("b2", "BootNotification", BOOT), timeout=3)
    assert primary.actions().count("BootNotification") == 2  # a later boot is a real one


async def test_a_stale_wake_up_does_not_cut_the_next_retry_delay_short(channels):
    channel = channels()
    channel._wake.set()  # an attach happened while the worker was not sleeping
    started = asyncio.get_running_loop().time()

    await channel._backoff_sleep(0.3)

    assert asyncio.get_running_loop().time() - started >= 0.25


async def test_a_live_call_waits_for_the_primary_to_reconnect(primary, attached):
    channel = await attached()
    port = primary.port
    await primary.stop()
    await eventually(lambda: not channel.connected)
    pending = asyncio.create_task(channel.call(Call("a", "Authorize", {"idTag": "04A2B3C4"}), timeout=5))
    await asyncio.sleep(0.1)
    assert not pending.done()

    recovered = await FakeCsms().start(port)
    try:
        assert await pending == CallResult("a", {"idTagInfo": {"status": "Accepted"}})
    finally:
        await channel.close()
        await recovered.stop()


@pytest.mark.parametrize(
    ("action", "payload", "reason"),
    [
        ("StopTransaction", {"transactionId": 1}, Reason.QUEUED),
        ("StatusNotification", {"connectorId": 1, "status": "Available"}, Reason.KEPT_LATEST),
        ("Heartbeat", {}, Reason.HEARTBEAT_LOCAL),
        ("MeterValues", {"connectorId": 1}, Reason.DROPPED_UNAVAILABLE),
    ],
)
async def test_what_happens_to_a_call_the_primary_cannot_take(channels, action, payload, reason):
    flow = RecordingFlow()
    call = Call("1", action, payload)

    reply = channels(flow=flow)._unavailable(call)

    [decision] = flow.decisions
    assert (decision.peer, decision.call, decision.reason) == ("primary", call, reason)
    if action == "Heartbeat":  # no CallError: the charger keeps its clock in sync
        assert isinstance(reply, CallResult)
        assert "currentTime" in reply.payload


async def test_a_queued_call_the_primary_rejects(channels):
    flow = RecordingFlow()
    call = Call("st", "StopTransaction", {"transactionId": 1})

    channels(flow=flow)._log_rejection(call, CallError("x", "FormationViolation"))

    assert flow.decisions[-1] == Decision("primary", call, Reason.QUEUED_REJECTED, "FormationViolation")


def _up_notes(flow: RecordingFlow) -> list[str | None]:
    return [note for peer, up, note in flow.link_notes if peer == "primary" and up]


def _down_notes(flow: RecordingFlow) -> list[str | None]:
    return [note for peer, up, note in flow.link_notes if peer == "primary" and not up]


async def _reconnected(primary, channel, flow: RecordingFlow) -> None:
    """Drop the primary connection and wait until the worker has a new one."""
    await primary.drop_connection()
    await eventually(lambda: flow.links_of("primary") == [True, False, True])
    await channel.call(Call("hb", "Heartbeat", {}), timeout=3)  # goes out once any replay is over


async def test_a_reconnect_within_the_grace_period_skips_the_boot_replay(primary, attached, tmp_path):
    _cache_boot(tmp_path)
    flow = RecordingFlow()
    channel = await attached(flow, boot_replay_grace=60)
    await primary.wait_for_call("BootNotification")
    await _reconnected(primary, channel, flow)

    await primary.wait_for_call("Heartbeat")
    assert primary.actions().count("BootNotification") == 1
    first, second = _up_notes(flow)
    assert first == "boot replayed"
    assert second.startswith("boot replay skipped: down ")


async def test_a_reconnect_after_the_grace_period_replays_the_boot(primary, attached, tmp_path):
    _cache_boot(tmp_path)
    flow = RecordingFlow()
    channel = await attached(flow, boot_replay_grace=0.01)  # the reconnect takes longer
    await primary.wait_for_call("BootNotification")
    await _reconnected(primary, channel, flow)

    await primary.wait_for_call("BootNotification", count=2)
    assert _up_notes(flow) == ["boot replayed", "boot replayed"]


async def test_a_link_whose_replay_was_rejected_does_not_count_as_booted(primary, attached, tmp_path):
    primary.responder = responding(
        BootNotification={"status": "Rejected", "currentTime": "2026-01-01T00:00:00Z", "interval": 60}
    )
    _cache_boot(tmp_path)
    flow = RecordingFlow()
    channel = await attached(flow, boot_replay_grace=60)
    await primary.wait_for_call("BootNotification")
    await _reconnected(primary, channel, flow)

    await primary.wait_for_call("BootNotification", count=2)


async def test_the_chargers_own_accepted_boot_lets_a_quick_reconnect_skip_the_replay(primary, attached):
    flow = RecordingFlow()
    channel = await attached(flow, boot_replay_grace=60)  # nothing cached yet
    reply = await channel.call(Call("b1", "BootNotification", BOOT), timeout=3)
    assert reply.payload["status"] == "Accepted"
    await _reconnected(primary, channel, flow)

    await primary.wait_for_call("Heartbeat")
    assert primary.actions().count("BootNotification") == 1
    assert _up_notes(flow)[-1].startswith("boot replay skipped: down ")


async def test_a_link_given_up_on_a_queued_call_says_so(primary, attached):
    primary.responder = responding(StopTransaction=None)
    flow = RecordingFlow()
    channel = await attached(flow, call_timeout=0.2)
    channel._unavailable(Call("s1", "StopTransaction", {"transactionId": 1}))

    await eventually(lambda: _down_notes(flow))
    assert _down_notes(flow)[0].startswith("closed by proxy: queued StopTransaction not answered in time · up ")


async def test_a_parked_link_is_closed_as_idle(channels, primary):
    flow = RecordingFlow()
    channel = channels(primary.url, flow)
    channel.detach(await channel.attach(channel._identity, _ignore))

    await eventually(lambda: _down_notes(flow))
    assert _down_notes(flow)[0].startswith("closed by proxy: idle: no session and nothing queued · up ")


async def test_closing_the_channel_names_the_shutdown(attached):
    flow = RecordingFlow()
    channel = await attached(flow)
    await channel.close()

    assert _down_notes(flow)[0].startswith("closed by proxy: proxy shutting down · up ")
