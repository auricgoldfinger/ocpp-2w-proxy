from __future__ import annotations

import asyncio

from conftest import CHARGER_ID, make_raw_config
from fakes import FakeCsms

from ocpp_2w_proxy import backoff
from ocpp_2w_proxy.charger_auth import ChargerIdentity
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult
from ocpp_2w_proxy.primary_channel import PrimaryChannel
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog


async def test_primary_queue_overflow_drops_meter_values_first(tmp_path):
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none", "max_queue": 2}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    try:
        channel._unavailable(Call("m0", "MeterValues", {"connectorId": 1, "transactionId": 1}))
        channel._unavailable(Call("st1", "StopTransaction", {"transactionId": 1}))
        channel._unavailable(Call("m1", "MeterValues", {"connectorId": 1, "transactionId": 2}))
        channel._unavailable(Call("st2", "StopTransaction", {"transactionId": 2}))

        # The meter readings are sacrificed so the billing-critical stops survive.
        assert [call.id for call in channel._queue] == ["st1", "st2"]
        assert [call["id"] for call in store.state.primary_outbox] == ["st1", "st2"]
    finally:
        await channel.close()


async def test_primary_queue_overflow_drops_oldest_when_no_meter_values(tmp_path):
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none", "max_queue": 2}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    try:
        for message_id in ("one", "two", "three"):
            channel._unavailable(Call(message_id, "StopTransaction", {"transactionId": 1}))

        assert [call.id for call in channel._queue] == ["two", "three"]
    finally:
        await channel.close()


async def test_outage_keeps_only_the_newest_status_and_drops_stale_readings(tmp_path):
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    try:
        for message_id, status in (("a", "Preparing"), ("b", "Charging")):
            channel._unavailable(Call(message_id, "StatusNotification", {"connectorId": 1, "status": status}))
        channel._unavailable(Call("c", "StatusNotification", {"connectorId": 2, "status": "Available"}))
        reply = channel._unavailable(Call("m", "MeterValues", {"connectorId": 1}))

        assert reply == CallResult("m", {})
        assert not channel._queue
        assert sorted(call.id for call in channel._latest.values()) == ["b", "c"]
        assert sorted(item["id"] for item in store.state.primary_latest.values()) == ["b", "c"]
    finally:
        await channel.close()


async def test_live_calls_fail_while_the_primary_is_down(tmp_path):
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    try:
        for action in ("Authorize", "StartTransaction", "DataTransfer"):
            assert isinstance(channel._unavailable(Call("x", action, {})), CallError)
        assert not channel._queue
        assert not channel._latest
    finally:
        await channel.close()


async def test_live_queued_actions_wait_behind_the_outbox(primary, tmp_path):
    """A StatusNotification sent live while an older queued stop is still unconfirmed
    would overtake it on the primary: keep it behind the stop instead."""
    primary.responder = lambda action, payload: (
        None if action == "StopTransaction" else FakeCsms().responder(action, payload)
    )
    config = parse(make_raw_config(primary.url, None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    channel = PrimaryChannel(
        config.primary,
        charger,
        StateStore.for_charger(config.proxy.state_dir, CHARGER_ID),
        TrafficLog(CHARGER_ID, False),
    )
    try:

        async def on_call(call: Call) -> None:
            pass

        await channel.attach(ChargerIdentity(charger, None, None), on_call)
        channel._queue.append(Call("old", "StopTransaction", {"transactionId": 1}))  # left over from an outage

        status = await channel.call(
            Call("new", "StatusNotification", {"connectorId": 1, "status": "Available"}), timeout=3
        )
        assert status == CallResult("new", {})  # acknowledged locally, not sent out of order
        assert [call.id for call in channel._latest.values()] == ["new"]

        # Actions that are never queued still go out live.
        heartbeat = await channel.call(Call("hb", "Heartbeat", {}), timeout=3)
        assert heartbeat.payload["currentTime"] == "2026-01-01T00:00:00Z"
        await primary.wait_for_call("StopTransaction")
        await primary.wait_for_call("Heartbeat")
        assert "StatusNotification" not in primary.actions()
    finally:
        await channel.close()


async def test_overflow_never_removes_the_call_being_sent(tmp_path):
    """The drain holds the queue head while awaiting the primary's reply; an
    overflow then must drop another call, or the drain's popleft() afterwards
    would discard an unsent message."""
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none", "max_queue": 2}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    try:
        calls = [Call(i, "MeterValues", {"connectorId": 1, "transactionId": 1}) for i in ("a", "b", "c")]
        channel._queue.extend(calls)
        channel._in_flight = calls[0]  # the drain is waiting for its reply right now

        channel._enforce_queue_limit()

        assert channel._queue[0] is calls[0]  # the in-flight head survives
        assert calls[1] not in channel._queue  # the oldest droppable meter went instead
        assert len(channel._queue) == 2
    finally:
        await channel.close()


async def test_heartbeat_is_answered_locally_while_the_primary_is_down(tmp_path):
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    channel = PrimaryChannel(
        config.primary,
        charger,
        StateStore.for_charger(config.proxy.state_dir, CHARGER_ID),
        TrafficLog(CHARGER_ID, False),
    )
    try:
        reply = channel._unavailable(Call("hb", "Heartbeat", {}))

        assert isinstance(reply, CallResult)
        assert "currentTime" in reply.payload  # no CallError: the charger keeps its clock in sync
    finally:
        await channel.close()


async def test_unreadable_queued_entries_are_skipped_at_startup(tmp_path):
    """One malformed outbox entry must not crash startup: the rest of the queue
    (billing data) still gets restored and delivered."""
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    store.state.primary_outbox = [
        {"id": "broken"},  # no action / payload
        {"id": "ok", "action": "MeterValues", "payload": {"connectorId": 1}},
    ]
    store.save()
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    try:
        assert [call.id for call in channel._queue] == ["ok"]
    finally:
        await channel.close()


async def test_reconnect_replays_the_cached_boot(primary, tmp_path):
    """Backends that expect a BootNotification on every new connection must get
    one when the worker reconnects mid-session."""
    config = parse(make_raw_config(primary.url, None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    channel = PrimaryChannel(
        config.primary,
        charger,
        StateStore.for_charger(config.proxy.state_dir, CHARGER_ID),
        TrafficLog(CHARGER_ID, False),
    )
    try:

        async def on_call(call: Call) -> None:
            pass

        await channel.attach(ChargerIdentity(charger, None, None), on_call)
        boot = await channel.call(Call("b", "BootNotification", {"chargePointVendor": "Grubby"}), timeout=3)
        assert boot.payload["status"] == "Accepted"
        await primary.wait_for_call("BootNotification")  # the charger's own boot

        await primary.drop_connection()  # the primary connection drops; the worker reconnects

        boots = await primary.wait_for_call("BootNotification", count=2)
        assert boots[-1]["chargePointVendor"] == "Grubby"  # the cached boot was replayed
    finally:
        await channel.close()


async def test_stale_detach_cannot_unhook_the_newer_session(primary, tmp_path):
    """UC-002 BR-004: a replaced session that outlives the replacement grace still runs
    detach() during its cleanup; that must not unhook the newer session's routing."""
    config = parse(make_raw_config(primary.url, None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    routed = []
    try:

        async def on_call_a(call: Call) -> None:
            routed.append("a")

        async def on_call_b(call: Call) -> None:
            routed.append("b")

        token_a = await channel.attach(ChargerIdentity(charger, None, None), on_call_a)
        token_b = await channel.attach(ChargerIdentity(charger, None, None), on_call_b)

        channel.detach(token_a)  # the stale session's cleanup, after the grace expired
        await channel._dispatch_call(Call("stale", "Heartbeat", {}))
        assert routed == ["b"]

        channel.detach(token_b)  # the current session ends normally
        await channel._dispatch_call(Call("after", "Heartbeat", {}))
        assert routed == ["b"]
    finally:
        await channel.close()


async def test_attach_during_backoff_sleep_is_served_and_drained(primary, tmp_path, monkeypatch):
    """A session attaching while the worker waits out a long retry is served at once.

    The link attach() returns must have a live reader, and the worker must wake from its
    backoff to drain the outbox; otherwise calls time out against a healthy primary.
    """
    monkeypatch.setattr(backoff, "MIN_RETRY_DELAY", 30.0)
    monkeypatch.setattr(backoff, "MAX_RETRY_DELAY", 60.0)
    port = primary.port
    await primary.stop()  # the worker's first connect attempt fails; it then sleeps ~30s

    store = StateStore.for_charger(tmp_path, CHARGER_ID)
    store.state.primary_outbox.append({"id": "q1", "action": "MeterValues", "payload": {"connectorId": 1}})
    store.save()
    config = parse(make_raw_config(f"ws://127.0.0.1:{port}/ocpp", None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    channel = PrimaryChannel(config.primary, charger, store, TrafficLog(CHARGER_ID, False))
    channel.start_background()
    await asyncio.sleep(0.05)  # let the worker reach its backoff sleep

    async def on_call(call: Call) -> None:
        pass

    recovered = await FakeCsms().start(port)
    try:
        await channel.attach(ChargerIdentity(charger, None, None), on_call)
        reply = await asyncio.wait_for(channel.call(Call("hb", "Heartbeat", {}), timeout=3), 5)
        assert isinstance(reply, CallResult)
        await asyncio.wait_for(recovered.wait_for_call("MeterValues"), 5)
    finally:
        await channel.close()
        await recovered.stop()


async def _attached_channel(primary, tmp_path):
    config = parse(make_raw_config(primary.url, None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    channel = PrimaryChannel(
        config.primary,
        charger,
        StateStore.for_charger(config.proxy.state_dir, CHARGER_ID),
        TrafficLog(CHARGER_ID, False),
    )

    async def on_call(call: Call) -> None:
        pass

    await channel.attach(ChargerIdentity(charger, None, None), on_call)
    return channel


START = {"connectorId": 1, "idTag": "tag", "meterStart": 0, "timestamp": "2026-01-01T00:00:00Z"}


async def test_start_transaction_waits_for_the_queued_stop_of_the_previous_one(primary, tmp_path):
    channel = await _attached_channel(primary, tmp_path)
    try:
        channel._unavailable(Call("old", "StopTransaction", {"transactionId": 1, "meterStop": 5}))

        reply = await channel.call(Call("new", "StartTransaction", START), timeout=3)

        assert isinstance(reply, CallResult)
        assert primary.actions() == ["StopTransaction", "StartTransaction"]
    finally:
        await channel.close()


async def test_start_transaction_is_refused_while_the_queue_does_not_drain(primary, tmp_path):
    primary.responder = lambda action, payload: None if action == "StopTransaction" else FakeCsms().responder(action, payload)
    channel = await _attached_channel(primary, tmp_path)
    try:
        channel._unavailable(Call("old", "StopTransaction", {"transactionId": 1, "meterStop": 5}))

        reply = await channel.call(Call("new", "StartTransaction", START), timeout=0.3)

        assert isinstance(reply, CallError)
        assert "StartTransaction" not in primary.actions()
    finally:
        await channel.close()
