"""ChargerSession.send_command: the proxy's own commands to a connected charger."""

from __future__ import annotations

import asyncio

import pytest
from conftest import CHARGER_ID
from fakes import ErrorReply, FakeCharger

from ocpp_2w_proxy.debug_target import ChargerGone, CommandTimeout
from ocpp_2w_proxy.ocpp import CallError, CallResult


async def connected(primary, start_proxy, proxies, **kwargs):
    url = await start_proxy(primary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID, **kwargs)
    await primary.connected.wait()
    while (session := proxies[-1].session_for(CHARGER_ID)) is None:
        await asyncio.sleep(0.01)
    return charger, session


async def test_the_charger_answer_comes_back_under_the_id_that_went_out(primary, start_proxy, proxies):
    charger, session = await connected(
        primary, start_proxy, proxies, responder=lambda action, payload: {"status": "Accepted", "echo": payload}
    )

    outcome = await session.send_command("Reset", {"type": "Soft"}, 5)

    assert charger.received_calls == [[2, outcome.sent.id, "Reset", {"type": "Soft"}]]
    assert outcome.reply == CallResult(outcome.sent.id, {"status": "Accepted", "echo": {"type": "Soft"}})
    assert outcome.latency_ms >= 0
    assert primary.actions() == []  # never reaches a backend
    await charger.close()


async def test_a_charger_call_error_is_the_outcome_not_an_exception(primary, start_proxy, proxies):
    charger, session = await connected(
        primary, start_proxy, proxies, responder=lambda action, payload: ErrorReply("NotSupported", "no")
    )

    outcome = await session.send_command("UpdateFirmware", {}, 5)

    assert outcome.reply == CallError(outcome.sent.id, "NotSupported", "no")
    await charger.close()


async def test_a_silent_charger_times_out_and_the_session_stays_healthy(primary, start_proxy, proxies):
    silent = [True]
    charger, session = await connected(
        primary, start_proxy, proxies, responder=lambda action, payload: None if silent[0] else {"status": "Accepted"}
    )

    with pytest.raises(CommandTimeout) as timeout:
        await session.send_command("Reset", {"type": "Soft"}, 0.1)
    assert timeout.value.sent.action == "Reset"

    silent[0] = False
    assert (await session.send_command("Reset", {"type": "Soft"}, 5)).reply.payload == {"status": "Accepted"}
    await charger.call("Heartbeat", {})  # the charger's own traffic still flows
    await charger.close()


async def test_a_disconnect_in_flight_fails_the_command(primary, start_proxy, proxies):
    charger, session = await connected(primary, start_proxy, proxies, responder=lambda action, payload: None)

    command = asyncio.create_task(session.send_command("Reset", {"type": "Soft"}, 30))
    while not charger.received_calls:
        await asyncio.sleep(0.01)
    await charger.close()

    with pytest.raises(ChargerGone):
        await command


async def test_a_command_to_a_session_that_has_ended_fails(primary, start_proxy, proxies):
    charger, session = await connected(primary, start_proxy, proxies)
    await charger.close()
    while proxies[-1].session_for(CHARGER_ID) is not None:
        await asyncio.sleep(0.01)

    with pytest.raises(ChargerGone):
        await session.send_command("Reset", {}, 1)


async def test_the_server_lists_chargers_and_exposes_live_sessions_only(primary, start_proxy, proxies):
    url = await start_proxy(primary.url)
    proxy = proxies[-1]
    assert proxy.charger_ids() == (CHARGER_ID,)
    assert proxy.session_for(CHARGER_ID) is None

    charger = await FakeCharger.connect(url, CHARGER_ID)
    while (session := proxy.session_for(CHARGER_ID)) is None:
        await asyncio.sleep(0.01)

    assert session.charger_id == CHARGER_ID
    assert session.remote.startswith("127.0.0.1:")
    assert session.connected_since.endswith("Z")
    assert proxy.session_for("other") is None
    await charger.close()
