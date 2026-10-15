"""What a charger session reports about the messages it relays, answers itself or loses."""

from __future__ import annotations

import pytest
from conftest import CHARGER_ID
from fakes import FakeCharger
from test_proxy_integration import START, eventually

from ocpp_2w_proxy.debug_target import CommandTimeout
from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.reasons import Reason
from ocpp_2w_proxy.session import ChargerSession


async def attached_charger(url, **kwargs) -> FakeCharger:
    charger = await FakeCharger.connect(url, CHARGER_ID, **kwargs)
    await charger.call("Heartbeat", {})  # the session (and its routing) is attached by now
    return charger


async def test_the_charger_link_is_reported_up_and_down(primary, start_proxy, flow):
    charger = await attached_charger(await start_proxy(primary.url))
    assert flow.links == [("charger", True)]

    await charger.close()
    await eventually(lambda: flow.links == [("charger", True), ("charger", False)])


@pytest.mark.parametrize(
    ("action", "payload", "reason"),
    [
        ("SetChargingProfile", {"connectorId": 1}, Reason.POLICY_ANSWERED),
        ("CustomVendorThing", {}, Reason.POLICY_REFUSED),
        ("RemoteStopTransaction", {"transactionId": 12345}, Reason.UNKNOWN_TRANSACTION),
    ],
)
async def test_a_secondary_command_the_proxy_answers_itself(
    primary, secondary, start_proxy, flow, action, payload, reason
):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    await secondary.call(action, payload)

    [decision] = flow.decisions
    assert (decision.peer, decision.call.action, decision.reason) == ("tap", action, reason)
    assert charger.received_calls == []
    await charger.close()


async def test_a_command_for_a_closed_charger_socket(primary, secondary, start_proxy, flow, monkeypatch):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))
    real_send = ChargerSession._send_to_charger

    async def socket_closed_for_commands(self, message):
        if isinstance(message, Call):
            return False  # as if the charger's socket closed right now
        return await real_send(self, message)

    monkeypatch.setattr(ChargerSession, "_send_to_charger", socket_closed_for_commands)
    await secondary.call("GetConfiguration", {"key": ["HeartbeatInterval"]})

    [decision] = flow.decisions
    assert (decision.peer, decision.call.action, decision.reason) == (
        "charger",
        "GetConfiguration",
        Reason.CHARGER_DISCONNECTED,
    )
    await charger.close()


async def test_a_malformed_charger_reply(primary, secondary, start_proxy, flow):
    responder = lambda action, payload: ["not", "an", "object"]  # noqa: E731
    charger = await attached_charger(await start_proxy(primary.url, secondary.url), responder=responder)

    await secondary.call("GetConfiguration", {"key": ["HeartbeatInterval"]})

    [decision] = flow.decisions
    assert (decision.peer, decision.reason) == ("charger", Reason.MALFORMED_FRAME)
    assert decision.call.id == charger.received_calls[0][1]
    await charger.close()


async def test_an_unauthorized_id_tag_is_withheld_from_the_primary(primary, secondary, start_proxy, flow):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    await charger.call("Authorize", {"idTag": "0000"})

    [decision] = flow.decisions
    assert (decision.peer, decision.call.action, decision.reason) == (
        "primary",
        "Authorize",
        Reason.WITHHELD_UNAUTHORIZED,
    )
    await charger.close()


async def test_a_start_the_primary_did_not_confirm_is_withheld_from_the_secondaries(
    primary, secondary, start_proxy, flow
):
    primary.responder = lambda action, payload: {"idTagInfo": {"status": "Blocked"}}
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    await charger.call("StartTransaction", START)

    [decision] = flow.decisions
    assert (decision.peer, decision.call.action, decision.reason) == (
        "tap",
        "StartTransaction",
        Reason.START_NOT_CONFIRMED,
    )
    await charger.close()


async def test_a_debug_command_the_charger_never_answers(primary, start_proxy, proxies, flow):
    charger = await attached_charger(await start_proxy(primary.url), responder=lambda action, payload: None)
    session = proxies[-1].session_for(CHARGER_ID)

    with pytest.raises(CommandTimeout) as timeout:
        await session.send_command("Reset", {"type": "Soft"}, 0.1)

    assert flow.reasons() == [Reason.DEBUG_COMMAND, Reason.CHARGER_NO_REPLY]
    assert all(decision.call is timeout.value.sent for decision in flow.decisions)
    await charger.close()
