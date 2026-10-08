from __future__ import annotations

import asyncio

from conftest import CHARGER_ID, make_raw_config
from fakes import FakeCsms

from ocpp_2w_proxy import backoff, primary_channel
from ocpp_2w_proxy.charger_auth import ChargerIdentity
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.primary_channel import PrimaryChannel
from ocpp_2w_proxy.state import StateStore
from ocpp_2w_proxy.traffic_log import TrafficLog


async def test_primary_queue_drops_oldest_when_full(tmp_path, monkeypatch):
    monkeypatch.setattr(primary_channel, "MAX_QUEUE", 2)
    config = parse(make_raw_config("ws://127.0.0.1:1", None, tmp_path, primary={"auth": "none"}), {})
    charger = config.chargers[CHARGER_ID]
    store = StateStore.for_charger(config.proxy.state_dir, CHARGER_ID)
    channel = PrimaryChannel(
        config.primary,
        charger,
        store,
        TrafficLog(CHARGER_ID, False),
    )
    try:
        for message_id in ("one", "two", "three"):
            channel._unavailable(Call(message_id, "MeterValues", {"value": message_id}))

        assert [call.id for call in channel._queue] == ["two", "three"]
        assert [call["id"] for call in store.state.primary_outbox] == ["two", "three"]
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
