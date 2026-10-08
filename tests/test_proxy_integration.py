"""End-to-end: fake charger <-> proxy <-> fake primary (Tap) + fake secondary backends."""

from __future__ import annotations

import asyncio
import base64
import json
import logging

import pytest
from conftest import CHARGER_ID, make_raw_config
from fakes import FakeCharger, FakeCsms
from websockets.exceptions import ConnectionClosed, InvalidStatus

from ocpp_2w_proxy import server as server_module
from ocpp_2w_proxy.config import parse
from ocpp_2w_proxy.server import ProxyServer
from ocpp_2w_proxy.session import ChargerSession

BOOT = {"chargePointVendor": "SolarEdge", "chargePointModel": "ONE"}
START = {"connectorId": 1, "idTag": "04A2B3C4", "meterStart": 0, "timestamp": "2026-01-01T10:00:00Z"}


def responder_with_transaction(transaction_id: int):
    def respond(action, payload):
        if action == "StartTransaction":
            return {"transactionId": transaction_id, "idTagInfo": {"status": "Accepted"}}
        return FakeCsms().responder(action, payload)

    return respond


async def eventually(predicate, timeout: float = 5) -> None:
    async def poll():
        while not predicate():
            await asyncio.sleep(0.02)

    await asyncio.wait_for(poll(), timeout)


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


async def test_transaction_ids_are_translated_for_the_secondary(primary, secondary, start_proxy):
    primary.responder = responder_with_transaction(100)
    secondary.responder = responder_with_transaction(9)
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()

    start = await charger.call("StartTransaction", START)
    assert start[2]["transactionId"] == 100
    await secondary.wait_for_call("StartTransaction")

    await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})

    assert (await secondary.wait_for_call("MeterValues"))[0]["transactionId"] == 9
    assert (await secondary.wait_for_call("StopTransaction"))[0]["transactionId"] == 9
    assert [p["transactionId"] for a, p in primary.calls if a == "StopTransaction"] == [100]
    await charger.close()


async def test_remote_stop_from_secondary_is_translated(primary, secondary, start_proxy):
    primary.responder = responder_with_transaction(100)
    secondary.responder = responder_with_transaction(9)
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()
    await charger.call("StartTransaction", START)
    await secondary.wait_for_call("StartTransaction")
    await asyncio.sleep(0.05)  # let the secondary's StartTransaction.conf be processed

    reply = await secondary.call("RemoteStopTransaction", {"transactionId": 9}, message_id="tap-1")
    assert reply == [3, "tap-1", {"status": "Accepted"}]
    assert charger.received_calls[-1][2:] == ["RemoteStopTransaction", {"transactionId": 100}]

    unknown = await secondary.call("RemoteStopTransaction", {"transactionId": 12345})
    assert unknown[2] == {"status": "Rejected"}
    assert len(charger.received_calls) == 1
    await charger.close()


async def test_conflicting_secondary_commands_never_reach_charger(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})  # the session (and its routing) is attached by now

    for action, expected in [
        ("SetChargingProfile", {"status": "Rejected"}),
        ("ClearChargingProfile", {"status": "Unknown"}),
        ("ChangeConfiguration", {"status": "Rejected"}),
        ("Reset", {"status": "Rejected"}),
        ("GetLocalListVersion", {"listVersion": -1}),
    ]:
        assert (await secondary.call(action, {"key": "HeartbeatInterval", "value": "1"}))[2] == expected
    assert (await secondary.call("CustomVendorThing", {}))[0] == 4
    assert charger.received_calls == []

    # The primary is allowed to do all of this.
    await primary.call("SetChargingProfile", {"connectorId": 1})
    assert charger.received_calls[0][2] == "SetChargingProfile"
    await charger.close()


async def test_colliding_message_ids_are_routed_to_the_right_backend(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(
        url, CHARGER_ID, responder=lambda action, payload: {"configurationKey": [{"key": payload["key"][0]}]}
    )
    await charger.call("Heartbeat", {})  # the session (and its routing) is attached by now

    from_primary, from_secondary = await asyncio.gather(
        primary.call("GetConfiguration", {"key": ["primary"]}, message_id="1"),
        secondary.call("GetConfiguration", {"key": ["secondary"]}, message_id="1"),
    )
    assert from_primary == [3, "1", {"configurationKey": [{"key": "primary"}]}]
    assert from_secondary == [3, "1", {"configurationKey": [{"key": "secondary"}]}]
    assert len({frame[1] for frame in charger.received_calls}) == 2
    await charger.close()


async def test_remote_start_from_secondary_is_refused(primary, secondary, start_proxy):
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})  # the session (and its routing) is attached by now
    reply = await secondary.call(
        "RemoteStartTransaction", {"idTag": "ABC", "chargingProfile": {"chargingProfileId": 1}}
    )
    assert reply[2] == {"status": "Rejected"}
    assert charger.received_calls == []

    # The primary may still start the same transaction remotely.
    await primary.call("RemoteStartTransaction", {"connectorId": 1, "idTag": "ABC"})
    assert charger.received_calls[0][2] == "RemoteStartTransaction"
    await charger.close()


async def test_secondary_outage_queues_and_replays_in_order(primary, start_proxy):
    primary.responder = responder_with_transaction(100)
    secondary = await FakeCsms(responder_with_transaction(9)).start()
    port = secondary.port
    await secondary.stop()  # Tap is down when the charger starts charging

    url = await start_proxy(primary.url, f"ws://127.0.0.1:{port}/ocpp")
    charger = await FakeCharger.connect(url, CHARGER_ID)
    assert (await charger.call("BootNotification", BOOT))[2]["status"] == "Accepted"
    await charger.call("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"})
    await charger.call("Heartbeat", {})
    assert (await charger.call("StartTransaction", START))[2]["transactionId"] == 100
    await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
    await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})

    secondary = await FakeCsms(responder_with_transaction(9)).start(port)
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


async def test_queue_survives_proxy_restart(primary, tmp_path):
    secondary = await FakeCsms(responder_with_transaction(9)).start()
    port = secondary.port
    await secondary.stop()
    secondary_url = f"ws://127.0.0.1:{port}/ocpp"

    config = parse(make_raw_config(primary.url, secondary_url, tmp_path), {"TAP_PASSWORD": "tap-secret"})
    proxy = ProxyServer(config)
    server = await proxy.start()
    url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ocpp"
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("BootNotification", BOOT)
    await charger.call("StartTransaction", START)
    await charger.close()
    await proxy.close()  # a real restart: the old proxy is gone

    secondary = await FakeCsms(responder_with_transaction(9)).start(port)
    try:
        proxy = ProxyServer(
            parse(make_raw_config(primary.url, secondary_url, tmp_path), {"TAP_PASSWORD": "tap-secret"})
        )
        await proxy.start()
        assert (await secondary.wait_for_call("StartTransaction"))[0]["idTag"] == START["idTag"]
        assert secondary.actions()[0] == "BootNotification"  # cached boot replayed first
        await proxy.close()
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
    dead = await FakeCsms().start()
    dead_url = dead.url
    await dead.stop()
    url = await start_proxy(dead_url, secondary.url)
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
    assert (await charger.call("StartTransaction", START))[0] == 4
    assert charger.ws.close_code is None
    # A start the primary never confirmed opens no session in the secondary: the
    # charger's retry must not stack phantom sessions there (UC-003 step 3).
    assert [action for action, _ in secondary.calls if action == "StartTransaction"] == []

    recovered = await FakeCsms().start(port)
    try:
        await recovered.wait_for_call("StatusNotification")
        # Billing data first, then the newest state report.
        assert recovered.actions() == ["MeterValues", "StatusNotification"]
        assert (await charger.call("Heartbeat", {}))[2] == {"currentTime": "2026-01-01T00:00:00Z"}
    finally:
        await charger.close()
        await recovered.stop()


async def test_primary_outbox_survives_proxy_restart(primary, tmp_path):
    port = primary.port
    primary_url = f"ws://127.0.0.1:{port}/ocpp"
    config = parse(make_raw_config(primary_url, None, tmp_path, primary={"auth": "none"}), {})
    proxy = ProxyServer(config)
    server = await proxy.start()
    url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ocpp"
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await primary.connected.wait()
    await primary.stop()

    assert (await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 20}))[2] == {}
    await charger.close()
    await proxy.close()

    recovered = await FakeCsms().start(port)
    restarted = ProxyServer(config)
    try:
        await restarted.start()
        await recovered.wait_for_call("StopTransaction")
        assert recovered.actions() == ["StopTransaction"]
    finally:
        await restarted.close()
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
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})  # the session is attached by now

    await secondary.send_raw(json.dumps([2, "y", "Heartbeat"]))
    await eventually(lambda: any(frame[:2] == [4, "y"] for frame in secondary.replies))
    frame = next(frame for frame in secondary.replies if frame[:2] == [4, "y"])
    assert frame[2] == "ProtocolError"
    await charger.close()


async def test_malformed_charger_reply_ends_the_backends_command_with_an_error(primary, secondary, start_proxy):
    """A broken CallResult is not answered (it is not a Call), but the backend that issued
    the command must not wait out its timeout: it gets an error right away."""
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID, responder=lambda action, payload: ["not", "an", "object"])
    await charger.call("Heartbeat", {})

    reply = await secondary.call("GetConfiguration", {"key": ["HeartbeatInterval"]})

    assert reply[0] == 4
    assert reply[2] == "GenericError"
    assert not any(frame[0] == 4 for frame in charger.frames)  # nothing was sent back to the charger
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


async def test_two_secondaries_get_independent_transaction_ids(primary, start_proxy):
    tap = await FakeCsms(responder_with_transaction(9)).start()
    stats = await FakeCsms(responder_with_transaction(77)).start()
    try:
        primary.responder = responder_with_transaction(100)
        url = await start_proxy(primary.url, [tap.url, stats.url])
        charger = await FakeCharger.connect(url, CHARGER_ID)
        await tap.connected.wait()
        await stats.connected.wait()

        start = await charger.call("StartTransaction", START)
        assert start[2]["transactionId"] == 100  # the primary's id goes to the charger
        await tap.wait_for_call("StartTransaction")
        await stats.wait_for_call("StartTransaction")
        await asyncio.sleep(0.05)  # let both backends' StartTransaction.conf be processed

        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
        await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})
        assert (await tap.wait_for_call("MeterValues"))[0]["transactionId"] == 9
        assert (await stats.wait_for_call("MeterValues"))[0]["transactionId"] == 77
        assert (await tap.wait_for_call("StopTransaction"))[0]["transactionId"] == 9
        assert (await stats.wait_for_call("StopTransaction"))[0]["transactionId"] == 77
        await charger.close()
    finally:
        await tap.stop()
        await stats.stop()


async def test_remote_stop_from_each_secondary_uses_its_own_transaction(primary, start_proxy):
    tap = await FakeCsms(responder_with_transaction(9)).start()
    stats = await FakeCsms(responder_with_transaction(77)).start()
    try:
        primary.responder = responder_with_transaction(100)
        url = await start_proxy(primary.url, [tap.url, stats.url])
        charger = await FakeCharger.connect(url, CHARGER_ID)
        await tap.connected.wait()
        await stats.connected.wait()
        await charger.call("StartTransaction", START)
        await tap.wait_for_call("StartTransaction")
        await stats.wait_for_call("StartTransaction")
        await asyncio.sleep(0.05)  # let both backends' StartTransaction.conf arrive

        from_tap = await tap.call("RemoteStopTransaction", {"transactionId": 9})
        from_stats = await stats.call("RemoteStopTransaction", {"transactionId": 77})
        assert from_tap[2] == {"status": "Accepted"}
        assert from_stats[2] == {"status": "Accepted"}
        # Each backend's stop refers to the same charger transaction: the primary's id 100.
        assert charger.received_calls[-2][3] == {"transactionId": 100}
        assert charger.received_calls[-1][3] == {"transactionId": 100}
        await charger.close()
    finally:
        await tap.stop()
        await stats.stop()


async def test_offline_secondary_does_not_delay_the_other(primary, start_proxy):
    offline = await FakeCsms(responder_with_transaction(77)).start()
    offline_port = offline.port
    await offline.stop()  # unreachable: its calls queue on disk
    tap = await FakeCsms(responder_with_transaction(9)).start()
    try:
        primary.responder = responder_with_transaction(100)
        url = await start_proxy(primary.url, [tap.url, f"ws://127.0.0.1:{offline_port}/ocpp"])
        charger = await FakeCharger.connect(url, CHARGER_ID)
        await tap.connected.wait()

        start = await charger.call("StartTransaction", START)
        assert start[2]["transactionId"] == 100  # the offline backend does not delay the charger
        assert (await tap.wait_for_call("StartTransaction"))[0]["idTag"] == START["idTag"]
        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
        assert (await tap.wait_for_call("MeterValues"))[0]["transactionId"] == 9

        # The charger session (and the healthy backend) stays untouched while the offline
        # backend recovers and its durable queue drains in order.
        recovered = await FakeCsms(responder_with_transaction(77)).start(offline_port)
        try:
            assert (await recovered.wait_for_call("StartTransaction"))[0]["idTag"] == START["idTag"]
            assert (await recovered.wait_for_call("MeterValues"))[0]["transactionId"] == 77
            assert (await charger.call("Heartbeat", {}))[2] == {"currentTime": "2026-01-01T00:00:00Z"}
        finally:
            await recovered.stop()
        await charger.close()
    finally:
        await tap.stop()


async def test_tx_profile_from_secondary_is_translated(primary, secondary, start_proxy):
    """A TxProfile the secondary sets names its own transactionId; the charger
    only knows the primary's, so the id inside the profile must be translated."""
    primary.responder = responder_with_transaction(100)
    secondary.responder = responder_with_transaction(9)
    url = await start_proxy(
        primary.url,
        secondary.url,
        primary={"policy": {"actions": {"SetChargingProfile": "answer"}}},
        secondary={"policy": {"actions": {"SetChargingProfile": "forward"}}},
    )
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await secondary.connected.wait()
    await charger.call("StartTransaction", START)
    await secondary.wait_for_call("StartTransaction")
    await asyncio.sleep(0.05)  # let the secondary's StartTransaction.conf be processed

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


async def test_case_insensitive_configuration_key_from_secondary(primary, secondary, start_proxy):
    """Configuration keys are CiStrings: any case spelling of an allowed key is
    forwarded, and no case spelling of an authorization key is."""
    url = await start_proxy(
        primary.url,
        secondary.url,
        primary={"policy": {"actions": {"ChangeConfiguration": "answer"}}},
        secondary={"policy": {"change_configuration_allow_keys": ["MeterValueSampleInterval"]}},
    )
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})  # the session (and its routing) is attached by now

    allowed = await secondary.call("ChangeConfiguration", {"key": "meterValueSampleInterval", "value": "30"})
    assert allowed[2] == {"status": "Accepted"}
    assert charger.received_calls[-1][2:] == ["ChangeConfiguration", {"key": "meterValueSampleInterval", "value": "30"}]

    denied = await secondary.call("ChangeConfiguration", {"key": "LOCALAUTHLISTENABLED", "value": "true"})
    assert denied[2] == {"status": "Rejected"}
    assert len(charger.received_calls) == 1
    await charger.close()


async def test_primary_forwarding_all_keys_cannot_touch_a_secondary_owned_key(primary, secondary, start_proxy):
    """The primary keeps ChangeConfiguration for every other key; the keys the secondary
    owns (e.g. a power limit) stay out of its reach."""
    url = await start_proxy(
        primary.url,
        secondary.url,
        secondary={"policy": {"change_configuration_allow_keys": ["MaxCurrent"]}},
    )
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})

    owned = await primary.call("ChangeConfiguration", {"key": "MaxCurrent", "value": "32"})
    assert owned[2] == {"status": "Rejected"}
    assert charger.received_calls == []

    other = await primary.call("ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"})
    assert other[2] == {"status": "Accepted"}
    assert charger.received_calls[-1][2:] == ["ChangeConfiguration", {"key": "HeartbeatInterval", "value": "60"}]
    await charger.close()


async def test_secondaries_deliver_while_the_charger_is_offline(primary, start_proxy):
    """The secondary channels belong to the server, not the session: queued billing
    data is delivered even while no charger is connected."""
    secondary = await FakeCsms(responder_with_transaction(9)).start()
    try:
        primary.responder = responder_with_transaction(100)
        url = await start_proxy(primary.url, secondary.url)
        charger = await FakeCharger.connect(url, CHARGER_ID)
        await charger.call("Heartbeat", {})  # the session is attached by now
        await charger.call("StartTransaction", START)
        await secondary.wait_for_call("StartTransaction")
        await asyncio.sleep(0.05)  # let the secondary's StartTransaction.conf create the mapping
        await secondary.drop_connection()  # the backend drops; its meter data queues durably

        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
        await charger.close()  # the charger goes offline too

        await secondary.connected.wait()  # the channel reconnects on its own, session or not
        assert (await secondary.wait_for_call("MeterValues"))[0]["transactionId"] == 9
    finally:
        await secondary.stop()


async def test_proxy_shutdown_closes_the_charger_connection_politely(primary, secondary, tmp_path):
    """A normal session end is not a server error: the socket closes with 1001
    (Going Away), not 1011, so chargers do not log a fault."""
    config = parse(make_raw_config(primary.url, secondary.url, tmp_path), {"TAP_PASSWORD": "tap-secret"})
    proxy = ProxyServer(config)
    server = await proxy.start()
    url = f"ws://127.0.0.1:{server.sockets[0].getsockname()[1]}/ocpp"
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})

    await proxy.close()

    await asyncio.wait_for(charger.ws.wait_closed(), 5)
    assert charger.ws.close_code == 1001


async def test_secondary_disconnects_while_the_charger_is_offline_and_returns_with_it(
    primary, secondary, start_proxy, caplog
):
    """A secondary that stayed connected would show an offline charger as online and
    could only answer its commands with errors."""
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})
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


async def test_refused_start_opens_no_secondary_session(primary, secondary, start_proxy):
    """The primary refuses the start; the charger retries. No secondary may have
    opened a session for the refused attempt, and the retry gets exactly one."""
    primary.responder = lambda action, payload: (
        {"idTagInfo": {"status": "Blocked"}} if action == "StartTransaction" else FakeCsms().responder(action, payload)
    )
    url = await start_proxy(primary.url, secondary.url)
    charger = await FakeCharger.connect(url, CHARGER_ID)
    await charger.call("Heartbeat", {})  # the session is attached by now
    try:
        refused = await charger.call("StartTransaction", START)
        assert refused[2] == {"idTagInfo": {"status": "Blocked"}}  # the primary's answer, verbatim
        await asyncio.sleep(0.05)
        assert [action for action, _ in secondary.calls if action == "StartTransaction"] == []

        primary.responder = responder_with_transaction(100)  # the charger retries
        retry = await charger.call("StartTransaction", START)
        assert retry[2]["transactionId"] == 100
        await secondary.wait_for_call("StartTransaction")
        assert len([action for action, _ in secondary.calls if action == "StartTransaction"]) == 1
    finally:
        await charger.close()


async def test_slow_backend_still_gets_its_stop_after_the_fast_one_stopped(primary, start_proxy):
    """The fast backend confirming its StopTransaction must not delete the slow
    backend's transaction mapping: that one still owes its own Stop."""
    stats = await FakeCsms(responder_with_transaction(77)).start()
    stats_port = stats.port
    await stats.stop()
    tap = await FakeCsms(responder_with_transaction(9)).start()
    try:
        primary.responder = responder_with_transaction(100)
        url = await start_proxy(primary.url, [tap.url, f"ws://127.0.0.1:{stats_port}/ocpp"])
        charger = await FakeCharger.connect(url, CHARGER_ID)
        await tap.connected.wait()
        await charger.call("StartTransaction", START)
        await tap.wait_for_call("StartTransaction")
        await asyncio.sleep(0.05)  # let tap's StartTransaction.conf be processed
        await charger.call("MeterValues", {"connectorId": 1, "transactionId": 100, "meterValue": []})
        await charger.call("StopTransaction", {"transactionId": 100, "meterStop": 7, "timestamp": "t"})
        await tap.wait_for_call("StopTransaction")
        await asyncio.sleep(0.05)  # let tap's StopTransaction.conf drop its own link

        stats = await FakeCsms(responder_with_transaction(77)).start(stats_port)
        try:
            await stats.wait_for_call("StartTransaction")
            assert (await stats.wait_for_call("MeterValues"))[0]["transactionId"] == 77
            assert (await stats.wait_for_call("StopTransaction"))[0]["transactionId"] == 77
        finally:
            await stats.stop()
        await charger.close()
    finally:
        await tap.stop()


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
