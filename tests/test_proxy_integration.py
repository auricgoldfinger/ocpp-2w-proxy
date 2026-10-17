"""End-to-end: fake charger <-> proxy <-> fake primary (Tap) + fake secondary backends."""

from __future__ import annotations

import asyncio
import base64
import json
import logging

import pytest
from conftest import CHARGER_ID, START, attached_charger, start_transaction
from fakes import FakeCharger, FakeCsms, dead_port, eventually, responding, with_transaction
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ocpp_2w_proxy import server as server_module
from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.primary_channel import PrimaryChannel
from ocpp_2w_proxy.reasons import Reason
from ocpp_2w_proxy.session import ChargerSession

BOOT = {"chargePointVendor": "SolarEdge", "chargePointModel": "ONE"}


async def test_charger_calls_reach_both_and_only_primary_reply_returns(primary, secondary, start_proxy):
    secondary.responder = lambda action, payload: {"status": "Rejected", "currentTime": "x", "interval": 5}
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID, password="from-charger")

    reply = await charger.call("BootNotification", BOOT)

    assert reply[2]["status"] == "Accepted"  # primary's answer, not the secondary's Rejected
    await secondary.wait_for_call("BootNotification")
    assert primary.paths == [f"/ocpp/{CHARGER_ID}"]
    assert secondary.paths == [f"/ocpp/{CHARGER_ID}"]
    # Primary gets the charger's own credentials (auth=forward); secondary only its own.
    assert base64.b64decode(primary.auth_headers[0].split()[1]) == b"CH1:from-charger"
    assert base64.b64decode(secondary.auth_headers[0].split()[1]) == b"CH1:tap-secret"
    await charger.close()


async def test_vendor_data_transfer_not_sent_to_secondary(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()
    await charger.call("DataTransfer", {"vendorId": "SolarEdge"})
    await charger.call("Heartbeat", {})
    await secondary.wait_for_call("Heartbeat")
    assert "DataTransfer" in primary.actions()
    assert "DataTransfer" not in secondary.actions()
    await charger.close()


async def test_unauthorized_session_is_withheld_from_the_primary(primary, secondary, start_proxy):
    primary.responder = with_transaction(100)
    secondary.responder = with_transaction(9)
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()

    start = await charger.call("StartTransaction", {**START, "idTag": "0" * 20})
    assert start[2] == {"transactionId": 2147483647, "idTagInfo": {"status": "Invalid"}}
    await secondary.wait_for_call("StartTransaction")

    await charger.call("MeterValues", {"connectorId": 1, "transactionId": 2147483647, "meterValue": []})
    await charger.call("StopTransaction", {"transactionId": 2147483647, "meterStop": 7, "timestamp": "t"})
    assert (await secondary.wait_for_call("MeterValues"))[0]["transactionId"] == 9
    assert (await secondary.wait_for_call("StopTransaction"))[0]["transactionId"] == 9

    # A real card afterwards still goes to the primary.
    assert (await charger.call("StartTransaction", START))[2]["transactionId"] == 100
    assert [p["idTag"] for a, p in primary.calls if a == "StartTransaction"] == [START["idTag"]]
    assert "MeterValues" not in primary.actions()
    assert "StopTransaction" not in primary.actions()
    await charger.close()


async def test_conflicting_secondary_commands_never_reach_charger(primary, secondary, start_proxy, flow):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    # The canned answers per action are test_policy's; this is the wiring.
    assert (await secondary.call("SetChargingProfile", {"connectorId": 1}))[2] == {"status": "Rejected"}
    remote_start = {"idTag": "ABC", "chargingProfile": {"chargingProfileId": 1}}
    assert (await secondary.call("RemoteStartTransaction", remote_start))[2] == {"status": "Rejected"}
    assert (await secondary.call("RemoteStopTransaction", {"transactionId": 12345}))[2] == {"status": "Rejected"}
    assert (await secondary.call("CustomVendorThing", {}))[0] == 4
    assert charger.received_calls == []
    assert [(d.peer, d.call.action, d.reason) for d in flow.decisions[-4:]] == [
        ("tap", "SetChargingProfile", Reason.POLICY_ANSWERED),
        ("tap", "RemoteStartTransaction", Reason.POLICY_ANSWERED),
        ("tap", "RemoteStopTransaction", Reason.UNKNOWN_TRANSACTION),
        ("tap", "CustomVendorThing", Reason.POLICY_REFUSED),
    ]

    # The primary is allowed to do all of this.
    await primary.call("SetChargingProfile", {"connectorId": 1})
    await primary.call("RemoteStartTransaction", {"connectorId": 1, "idTag": "ABC"})
    assert [call[2] for call in charger.received_calls] == ["SetChargingProfile", "RemoteStartTransaction"]
    await charger.close()


async def test_colliding_message_ids_are_routed_to_the_right_backend(primary, secondary, start_proxy):
    charger = await attached_charger(
        await start_proxy(primary.url, secondary.url),
        responder=lambda action, payload: {"configurationKey": [{"key": payload["key"][0]}]},
    )

    from_primary, from_secondary = await asyncio.gather(
        primary.call("GetConfiguration", {"key": ["primary"]}, message_id="1"),
        secondary.call("GetConfiguration", {"key": ["secondary"]}, message_id="1"),
    )
    assert from_primary == [3, "1", {"configurationKey": [{"key": "primary"}]}]
    assert from_secondary == [3, "1", {"configurationKey": [{"key": "secondary"}]}]
    assert len({frame[1] for frame in charger.received_calls}) == 2
    await charger.close()


async def test_secondary_outage_queues_and_replays_in_order(primary, start_proxy):
    primary.responder = with_transaction(100)
    port = await dead_port()  # Tap is down when the charger starts charging

    url = await start_proxy(primary.url, f"ws://127.0.0.1:{port}/ocpp")
    charger = await FakeCharger.connect(url, CHARGER_ID)
    assert (await charger.call("BootNotification", BOOT))[2]["status"] == "Accepted"
    await charger.call("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
    await charger.call("Heartbeat", {})
    assert (await charger.call("StartTransaction", START))[2]["transactionId"] == 100
    await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})

    secondary = await FakeCsms(with_transaction(9)).start(port)
    try:
        await secondary.wait_for_call("StopTransaction")
        assert secondary.actions() == [
            "BootNotification",
            "StatusNotification",
            "StartTransaction",
            "MeterValues",
            "StopTransaction",
        ]
        assert secondary.calls[3][1]["transactionId"] == 9
        assert secondary.calls[4][1]["transactionId"] == 9
    finally:
        await charger.close()
        await secondary.stop()


async def test_queue_survives_proxy_restart(primary, start_proxy, proxies):
    port = await dead_port()
    secondary_url = f"ws://127.0.0.1:{port}/ocpp"

    charger = await FakeCharger.connect(await start_proxy(primary.url, secondary_url), CHARGER_ID)
    await charger.call("BootNotification", BOOT)
    await charger.call("StartTransaction", START)
    await charger.close()
    await proxies[-1].close()  # a real restart: the old proxy is gone

    secondary = await FakeCsms(with_transaction(9)).start(port)
    try:
        await start_proxy(primary.url, secondary_url)
        assert (await secondary.wait_for_call("StartTransaction"))[0]["idTag"] == START["idTag"]
        assert secondary.actions()[0] == "BootNotification"  # cached boot replayed first
        await proxies[-1].close()
    finally:
        await secondary.stop()


async def test_secondary_disconnect_does_not_affect_charger(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()
    await secondary.drop_connection()
    assert (await charger.call("Heartbeat", {}))[2] == {"currentTime": "2026-01-01T00:00:00Z"}
    await secondary.connected.wait()  # proxy reconnects on its own
    await charger.close()


async def test_primary_unreachable_closes_charger(secondary, start_proxy):
    url = await start_proxy(f"ws://127.0.0.1:{await dead_port()}/ocpp", secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await asyncio.wait_for(charger.ws.wait_closed(), 5)
    assert charger.ws.close_code == 1011


async def test_primary_disconnect_queues_selected_calls_and_recovers(primary, secondary, start_proxy):
    port = primary.port
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await secondary.connected.wait()
    await primary.stop()

    assert (await charger.call("StatusNotification", {"connectorId": 1, "status": "Charging"}))[2] == {}
    assert (await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []}))[2] == {}
    # A reading that belongs to no transaction is stale by the time the primary is back.
    assert (await charger.call("MeterValues", {"connectorId": 1, "meterValue": []}))[2] == {}
    heartbeat = await charger.call("Heartbeat", {})
    assert heartbeat[0] == 3 and heartbeat[2]["currentTime"]  # answered locally, not an error
    assert charger.ws.close_code is None

    recovered = await FakeCsms().start(port)
    try:
        await recovered.wait_for_call("StatusNotification")
        # Billing data first, then the newest state report.
        assert recovered.actions() == ["MeterValues", "StatusNotification"]
        assert (await charger.call("Heartbeat", {}))[2] == {"currentTime": "2026-01-01T00:00:00Z"}
    finally:
        await charger.close()
        await recovered.stop()


async def test_a_start_the_primary_cannot_answer_goes_unanswered_and_closes_the_charger(
    primary, secondary, start_proxy, flow
):
    url = await start_proxy(primary.url, secondary.url, primary={"call_timeout": 1})
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await secondary.connected.wait()
    await primary.stop()

    await charger.send_raw(json.dumps([2, "start-1", "StartTransaction", START]))
    await asyncio.wait_for(charger.ws.wait_closed(), 5)

    # No substitute answer: the charger keeps the start and resends it once back online.
    assert charger.ws.close_code == 1011
    assert not [frame for frame in charger.frames if frame[0] in (3, 4)]
    # A start the primary never confirmed opens no session in the secondary (UC-003 step 3).
    assert [action for action, _ in secondary.calls if action == "StartTransaction"] == []
    [decision] = flow.with_reason(Reason.PRIMARY_UNAVAILABLE_CLOSE)
    assert (decision.peer, decision.call.id) == ("primary", "start-1")


async def test_a_primary_outage_longer_than_the_grace_period_closes_the_charger(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url, primary={"outage_grace": 0.3})
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await primary.stop()

    await asyncio.wait_for(charger.ws.wait_closed(), 5)
    assert charger.ws.close_code == 1011


async def test_a_primary_outage_within_the_grace_period_keeps_the_charger_connected(primary, start_proxy):
    port = primary.port
    url = await start_proxy(primary.url, primary={"outage_grace": 0.8})
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await primary.stop()
    await asyncio.sleep(0.3)

    recovered = await FakeCsms().start(port)
    try:
        await recovered.connected.wait()
        await asyncio.sleep(0.8)  # past the grace period that started with the outage
        assert charger.ws.close_code is None
        assert (await charger.call("Heartbeat", {}))[2] == {"currentTime": "2026-01-01T00:00:00Z"}
    finally:
        await charger.close()
        await recovered.stop()


async def test_primary_outbox_survives_proxy_restart(primary, start_proxy, proxies):
    port = primary.port
    charger = await FakeCharger.connect(await start_proxy(primary.url, primary={"auth": "none"}), CHARGER_ID)
    await primary.connected.wait()
    await primary.stop()

    assert (await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 20}))[2] == {}
    await charger.close()
    await proxies[-1].close()

    recovered = await FakeCsms().start(port)
    try:
        await start_proxy(recovered.url, primary={"auth": "none"})
        await recovered.wait_for_call("StopTransaction")
        assert recovered.actions() == ["StopTransaction"]
    finally:
        await proxies[-1].close()
        await recovered.stop()


async def test_malformed_charger_frame_gets_a_protocol_error_reply(primary, secondary, start_proxy):
    """A malformed frame must be answered with a ProtocolError CallError on its own
    message id, so the sender stops waiting for an answer it will never get."""
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)

    await charger.send_raw(json.dumps([2, "x", "Heartbeat"]))  # a Call missing its payload
    await eventually(lambda: any(frame[:2] == [4, "x"] for frame in charger.frames))
    frame = next(frame for frame in charger.frames if frame[:2] == [4, "x"])
    assert frame[2] == "ProtocolError"

    await charger.send_raw("this is not ocpp")  # not even the id is readable
    await eventually(lambda: any(frame[0] == 4 and frame[1] != "x" for frame in charger.frames))

    assert (await charger.call("Heartbeat", {}))[0] == 3  # the connection is unaffected
    await charger.close()


async def test_malformed_backend_frame_gets_a_protocol_error_reply(primary, secondary, start_proxy):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    await secondary.send_raw(json.dumps([2, "y", "Heartbeat"]))
    await eventually(lambda: any(frame[:2] == [4, "y"] for frame in secondary.replies))
    frame = next(frame for frame in secondary.replies if frame[:2] == [4, "y"])
    assert frame[2] == "ProtocolError"
    await charger.close()


async def test_a_charger_reusing_message_ids_does_not_collide_start_records(primary, secondary, start_proxy, tmp_path):
    """Chargers restart their message ids after a reboot. Each start is correlated under
    a proxy-made reference, so two starts with the same charger id keep separate records."""
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))
    try:
        for expected_calls, transaction_id in ((1, 100), (2, 101)):
            primary.responder = with_transaction(transaction_id)
            await charger.send_raw(json.dumps([2, "1", "StartTransaction", START]))  # the same id both times
            await primary.wait_for_call("StartTransaction", count=expected_calls)
            await secondary.wait_for_call("StartTransaction", count=expected_calls)

        state = json.loads((tmp_path / f"{CHARGER_ID}.json").read_text())
        assert sorted(state["pending_primary_starts"].values()) == [100, 101]
        assert "1" not in state["pending_primary_starts"]
    finally:
        await charger.close()


async def test_command_for_a_charger_whose_socket_just_closed_is_answered_with_an_error(
    primary, secondary, start_proxy, monkeypatch, flow
):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))
    real_send = ChargerSession._send_to_charger

    async def socket_closed_for_commands(self, message):
        if isinstance(message, Call):
            return False  # as if the charger's socket closed right now
        return await real_send(self, message)

    monkeypatch.setattr(ChargerSession, "_send_to_charger", socket_closed_for_commands)

    reply = await secondary.call("GetConfiguration", {"key": ["HeartbeatInterval"]})

    assert reply == [4, reply[1], "GenericError", "charger is disconnected", {}]
    [decision] = flow.with_reason(Reason.CHARGER_DISCONNECTED)
    assert (decision.peer, decision.call.action) == ("charger", "GetConfiguration")
    await charger.close()


async def test_a_failing_relay_is_logged(primary, secondary, start_proxy, monkeypatch, caplog):
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    async def broken(self, call, timeout):
        raise RuntimeError("boom")

    monkeypatch.setattr(PrimaryChannel, "call", broken)
    with caplog.at_level(logging.ERROR):
        await charger.send_raw(json.dumps([2, "x", "Heartbeat", {}]))
        await eventually(lambda: any("relaying a charger call failed" in r.getMessage() for r in caplog.records))
    await charger.close()


async def test_malformed_charger_reply_ends_the_backends_command_with_an_error(primary, secondary, start_proxy, flow):
    """A broken CallResult is not answered (it is not a Call), but the backend that issued
    the command must not wait out its timeout: it gets an error right away."""
    responder = lambda action, payload: ["not", "an", "object"]  # noqa: E731
    charger = await attached_charger(await start_proxy(primary.url, secondary.url), responder=responder)

    reply = await secondary.call("GetConfiguration", {"key": ["HeartbeatInterval"]})

    assert (reply[0], reply[2]) == (4, "GenericError")
    assert not any(frame[0] == 4 for frame in charger.frames)  # nothing was sent back to the charger
    [decision] = flow.with_reason(Reason.MALFORMED_FRAME)
    assert (decision.peer, decision.call.id) == ("charger", charger.received_calls[0][1])
    await charger.close()


@pytest.mark.parametrize(
    ("charger_id", "password", "status"),
    [("UNKNOWN", None, 404), (CHARGER_ID, "wrong", 401), (CHARGER_ID, None, 401)],
)
async def test_handshake_rejections(primary, secondary, start_proxy, charger_id, password, status):
    url = await start_proxy(
        primary.url, secondary.url, environ={"CHARGER_PW": "right"}, charger={"password_env": "CHARGER_PW"}
    )
    with pytest.raises(InvalidStatus) as exc:
        await FakeCharger.connect(url, charger_id, password=password)
    assert exc.value.response.status_code == status
    assert primary.paths == []


async def test_reconnecting_charger_replaces_old_session(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url)
    old = await FakeCharger.connect(url, CHARGER_ID)
    await old.call("Heartbeat", {})
    new = await FakeCharger.connect(url, CHARGER_ID)
    await asyncio.wait_for(old.ws.wait_closed(), 5)
    assert (await new.call("Heartbeat", {}))[0] == 3
    with pytest.raises(ConnectionClosed):
        await old.call("Heartbeat", {})
    await new.close()


async def test_simultaneous_handshakes_end_in_exactly_one_session(primary, secondary, start_proxy):
    """Two chargers connecting at the same moment must not both start a session:
    one registration would overwrite the other, leaving an untracked session
    nobody ever closes or replaces."""
    url = await start_proxy(primary.url, secondary.url)
    first, second = await asyncio.gather(
        FakeCharger.connect(url, CHARGER_ID),
        FakeCharger.connect(url, CHARGER_ID),
    )
    third = await FakeCharger.connect(url, CHARGER_ID)

    assert (await third.call("Heartbeat", {}))[0] == 3  # the surviving session answers
    for replaced in (first, second):
        await asyncio.wait_for(replaced.ws.wait_closed(), 5)  # both are closed
    await third.close()


async def test_each_secondary_gets_its_own_transaction_ids(primary, secondary, start_proxy, proxies):
    primary.responder = with_transaction(100)
    tap, stats = secondary, await FakeCsms(with_transaction(77)).start()
    tap.responder = with_transaction(9)
    try:
        charger = await FakeCharger.connect(await start_proxy(primary.url, [tap.url, stats.url]), CHARGER_ID)
        start = await start_transaction(charger, proxies[-1], tap=9, stats=77)
        assert start[2]["transactionId"] == 100  # the primary's id goes to the charger
        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
        assert (await tap.wait_for_call("MeterValues"))[0]["transactionId"] == 9
        assert (await stats.wait_for_call("MeterValues"))[0]["transactionId"] == 77

        # Each backend's stop refers to the same charger transaction: the primary's id 100.
        from_tap = await tap.call("RemoteStopTransaction", {"transactionId": 9}, message_id="tap-1")
        assert from_tap == [3, "tap-1", {"status": "Accepted"}]
        assert (await stats.call("RemoteStopTransaction", {"transactionId": 77}))[2] == {"status": "Accepted"}
        assert [call[2:] for call in charger.received_calls] == [["RemoteStopTransaction", {"transactionId": 100}]] * 2
        assert (await tap.call("RemoteStopTransaction", {"transactionId": 12345}))[2] == {"status": "Rejected"}
        assert len(charger.received_calls) == 2

        await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})
        assert (await tap.wait_for_call("StopTransaction"))[0]["transactionId"] == 9
        assert (await stats.wait_for_call("StopTransaction"))[0]["transactionId"] == 77
        assert [p["transactionId"] for a, p in primary.calls if a == "StopTransaction"] == [100]
        await charger.close()
    finally:
        await stats.stop()


async def test_tx_profile_from_secondary_is_translated(primary, secondary, start_proxy, proxies):
    """A TxProfile the secondary sets names its own transactionId; the charger
    only knows the primary's, so the id inside the profile must be translated."""
    primary.responder = with_transaction(100)
    secondary.responder = with_transaction(9)
    url = await start_proxy(
        primary.url,
        secondary.url,
        primary={"policy": {"actions": {"SetChargingProfile": "answer"}}},
        secondary={"policy": {"actions": {"SetChargingProfile": "forward"}}},
    )
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await start_transaction(charger, proxies[-1], tap=9)

    tx_profile = {
        "chargingProfileId": 1,
        "stackLevel": 1,
        "chargingProfilePurpose": "TxProfile",
        "transactionId": 9,
        "chargingSchedule": [],
    }
    reply = await secondary.call("SetChargingProfile", {"connectorId": 1, "csChargingProfiles": tx_profile})
    assert reply[2] == {"status": "Accepted"}
    assert charger.received_calls[-1][3]["csChargingProfiles"]["transactionId"] == 100

    # A profile for a transaction this backend does not know is rejected by the proxy.
    unknown = await secondary.call(
        "SetChargingProfile", {"connectorId": 1, "csChargingProfiles": {**tx_profile, "transactionId": 12345}}
    )
    assert unknown[2] == {"status": "Rejected"}
    await charger.close()


async def test_configuration_keys_a_secondary_owns(primary, secondary, start_proxy):
    """Configuration keys are CiStrings: any case spelling of a key the secondary owns is
    forwarded, and no case spelling of an authorization key is. The primary keeps
    ChangeConfiguration for every other key; the owned ones (e.g. a power limit) stay out of its reach."""
    url = await start_proxy(
        primary.url, secondary.url, secondary={"policy": {"change_configuration_allow_keys": ["MaxCurrent"]}}
    )
    charger = await attached_charger(url)

    assert (await secondary.call("ChangeConfiguration", {"key": "maxCurrent", "value": "16"}))[2] == {
        "status": "Accepted"
    }
    assert charger.received_calls[-1][2:] == ["ChangeConfiguration", {"key": "maxCurrent", "value": "16"}]
    denied = await secondary.call("ChangeConfiguration", {"key": "LOCALAUTHLISTENABLED", "value": "true"})
    assert denied[2] == {"status": "Rejected"}
    assert (await primary.call("ChangeConfiguration", {"key": "MaxCurrent", "value": "32"}))[2] == {
        "status": "Rejected"
    }
    assert len(charger.received_calls) == 1

    other = await primary.call("ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"})
    assert other[2] == {"status": "Accepted"}
    assert charger.received_calls[-1][2:] == ["ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"}]
    await charger.close()


async def test_secondaries_deliver_while_the_charger_is_offline(primary, secondary, start_proxy, proxies):
    """The secondary channels belong to the server, not the session: queued billing
    data is delivered even while no charger is connected."""
    primary.responder = with_transaction(100)
    secondary.responder = with_transaction(9)
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))
    await start_transaction(charger, proxies[-1], tap=9)
    await secondary.drop_connection()  # the backend drops; its meter data queues durably

    await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    await charger.close()  # the charger goes offline too

    await secondary.connected.wait()  # the channel reconnects on its own, session or not
    assert (await secondary.wait_for_call("MeterValues"))[0]["transactionId"] == 9


async def test_proxy_shutdown_closes_the_charger_connection_politely(primary, secondary, start_proxy, proxies):
    """A normal session end is not a server error: the socket closes with 1001
    (Going Away), not 1011, so chargers do not log a fault."""
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))

    await proxies[-1].close()

    await asyncio.wait_for(charger.ws.wait_closed(), 5)
    assert charger.ws.close_code == 1001


async def test_secondary_disconnects_while_the_charger_is_offline_and_returns_with_it(
    primary, secondary, start_proxy, caplog
):
    """A secondary that stayed connected would show an offline charger as online and
    could only answer its commands with errors."""
    url = await start_proxy(primary.url, secondary.url)
    charger = await attached_charger(url)
    await secondary.connected.wait()
    await charger.close()
    with caplog.at_level(logging.INFO):
        await eventually(lambda: any(r.getMessage() == f"{CHARGER_ID} session closed" for r in caplog.records))
    await eventually(lambda: not secondary.connected.is_set())

    again = await FakeCharger.connect(url, CHARGER_ID)
    try:
        await asyncio.wait_for(secondary.connected.wait(), 5)
    finally:
        await again.close()


async def test_refused_start_opens_no_secondary_session(primary, secondary, start_proxy, flow):
    """The primary refuses the start; the charger retries. No secondary may have
    opened a session for the refused attempt, and the retry gets exactly one."""
    primary.responder = responding(StartTransaction={"idTagInfo": {"status": "Blocked"}})
    charger = await attached_charger(await start_proxy(primary.url, secondary.url))
    try:
        refused = await charger.call("StartTransaction", START)
        assert refused[2] == {"idTagInfo": {"status": "Blocked"}}  # the primary's answer, verbatim
        await asyncio.sleep(0.05)
        assert [action for action, _ in secondary.calls if action == "StartTransaction"] == []
        [decision] = flow.with_reason(Reason.START_NOT_CONFIRMED)
        assert (decision.peer, decision.call.action) == ("tap", "StartTransaction")

        primary.responder = with_transaction(100)  # the charger retries
        retry = await charger.call("StartTransaction", START)
        assert retry[2]["transactionId"] == 100
        await secondary.wait_for_call("StartTransaction")
        assert len([action for action, _ in secondary.calls if action == "StartTransaction"]) == 1
    finally:
        await charger.close()


async def test_offline_secondary_delays_nothing_and_still_gets_its_stop(primary, secondary, start_proxy, proxies):
    """An unreachable backend's calls queue on disk without delaying the charger or the
    other backend. The fast backend confirming its StopTransaction must not delete the slow
    backend's transaction mapping: that one still owes its own Stop."""
    primary.responder = with_transaction(100)
    tap, stats_port = secondary, await dead_port()
    tap.responder = with_transaction(9)
    charger = await FakeCharger.connect(
        await start_proxy(primary.url, [tap.url, f"ws://127.0.0.1:{stats_port}/ocpp"]), CHARGER_ID
    )
    start = await start_transaction(charger, proxies[-1], tap=9)
    assert start[2]["transactionId"] == 100  # the offline backend does not delay the charger
    assert (await tap.wait_for_call("StartTransaction"))[0]["idTag"] == START["idTag"]
    await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    assert (await tap.wait_for_call("MeterValues"))[0]["transactionId"] == 9
    await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})
    transactions = proxies[-1].context_for(CHARGER_ID).transactions
    await eventually(lambda: transactions.to_secondary(100, "tap") is None)  # tap's Stop dropped its own link

    # The charger session (and the healthy backend) stays untouched while the offline
    # backend recovers and its durable queue drains in order.
    stats = await FakeCsms(with_transaction(77)).start(stats_port)
    try:
        assert (await stats.wait_for_call("StartTransaction"))[0]["idTag"] == START["idTag"]
        assert (await stats.wait_for_call("MeterValues"))[0]["transactionId"] == 77
        assert (await stats.wait_for_call("StopTransaction"))[0]["transactionId"] == 77
        assert (await charger.call("Heartbeat", {}))[2] == {"currentTime": "2026-01-01T00:00:00Z"}
    finally:
        await stats.stop()
    await charger.close()


async def test_five_secondary_backends_per_session(primary, start_proxy):
    backends = [await FakeCsms().start() for _ in range(5)]
    try:
        url = await start_proxy(primary.url, [backend.url for backend in backends])
        charger = await FakeCharger.connect(url, CHARGER_ID)
        await charger.call("BootNotification", BOOT)
        await charger.call("StatusNotification", {"connectorId": 1, "status": "Available", "errorCode": "NoError"})
        for backend in backends:
            await backend.wait_for_call("BootNotification")
            await backend.wait_for_call("StatusNotification")
        await charger.close()
    finally:
        for backend in backends:
            await backend.stop()


async def test_stale_session_cannot_unhook_its_replacement(primary, start_proxy, monkeypatch, caplog):
    """UC-002 BR-004: the old session gets 15 seconds to finish. When it outlives that
    grace, its cleanup still runs detach() — which must not unhook the replacement."""
    monkeypatch.setattr(server_module, "REPLACE_TIMEOUT_SECONDS", 0.05)
    real_read = ChargerSession._read_charger
    stale_read_hung = asyncio.Event()
    unblock_stale = asyncio.Event()
    first_session = True

    async def hang_first_read(self):
        nonlocal first_session
        if first_session:
            first_session = False
            stale_read_hung.set()
            await unblock_stale.wait()
        await real_read(self)

    monkeypatch.setattr(ChargerSession, "_read_charger", hang_first_read)

    with caplog.at_level(logging.INFO):
        url = await start_proxy(primary.url)
        await FakeCharger.connect(url, CHARGER_ID)
        await stale_read_hung.wait()  # the old session is attached and now stuck in its read loop
        new = await FakeCharger.connect(url, CHARGER_ID)  # replaces it; the grace expires at 0.05s
        assert (await new.call("Heartbeat", {}))[0] == 3

        unblock_stale.set()  # the stale session finally runs its cleanup, detach() included
        session_closed = f"{CHARGER_ID} session closed"
        await eventually(lambda: any(r.getMessage() == session_closed for r in caplog.records))

        # The stale session's detach() must not have unhooked the replacement.
        reply = await primary.call("Reset", {})
        assert reply[0] == 3
        assert new.received_calls[-1][2:] == ["Reset", {}]
        await new.close()
