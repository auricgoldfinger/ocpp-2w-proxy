"""End-to-end: HTTP debug endpoint -> proxy -> fake charger."""

from __future__ import annotations

import asyncio
import logging

from conftest import CHARGER_ID
from fakes import ErrorReply, FakeCharger, http_request

COMMANDS = f"/debug/chargers/{CHARGER_ID}/commands"
RESET = {"action": "Reset", "payload": {"type": "Soft"}}


async def start(primary, start_proxy, proxies, **kwargs):
    url = await start_proxy(primary.url, debug={}, **kwargs)
    return url, proxies[-1].debug_port


async def connect(url, proxies, **kwargs) -> FakeCharger:
    charger = await FakeCharger.connect(url, CHARGER_ID, **kwargs)
    while proxies[-1].session_for(CHARGER_ID) is None:
        await asyncio.sleep(0.01)
    return charger


async def test_a_command_reaches_the_charger_and_its_result_comes_back(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies)

    status, body = await http_request(port, "POST", COMMANDS, RESET)

    assert status == 200
    assert body["status"] == "CallResult" and body["answered_by"] == "charger"
    assert body["result"] == {"status": "Accepted"}
    assert body["action"] == "Reset" and body["latency_ms"] >= 0
    assert charger.received_calls == [[2, body["message_id"], "Reset", {"type": "Soft"}]]
    assert body["sent"] == charger.received_calls[0]
    assert body["received"] == [3, body["message_id"], {"status": "Accepted"}]
    assert primary.actions() == []  # never forwarded to a backend
    await charger.close()


async def test_a_call_error_from_the_charger_is_a_200_with_the_error(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies, responder=lambda action, payload: ErrorReply("NotSupported", "no way"))

    status, body = await http_request(port, "POST", COMMANDS, {"action": "UpdateFirmware"})

    assert status == 200
    assert body["status"] == "CallError"
    assert body["error"] == {"code": "NotSupported", "description": "no way", "details": {}}
    await charger.close()


async def test_a_silent_charger_is_a_504_and_the_session_stays_healthy(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    silent = [True]
    charger = await connect(
        url, proxies, responder=lambda action, payload: None if silent[0] else {"status": "Accepted"}
    )

    status, body = await http_request(port, "POST", COMMANDS, {**RESET, "timeout": 0.2})

    assert (status, body["error"]) == (504, "charger_timeout")
    assert body["sent"][2:] == ["Reset", {"type": "Soft"}]
    silent[0] = False
    assert (await http_request(port, "POST", COMMANDS, RESET))[0] == 200  # no longer busy
    assert (await charger.call("Heartbeat", {}))[0] == 3
    await charger.close()


async def test_a_charger_that_is_not_connected_is_a_404_and_nothing_is_queued(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)

    status, body = await http_request(port, "POST", COMMANDS, RESET)
    assert (status, body["error"]) == (404, "not_connected")

    charger = await connect(url, proxies)
    await asyncio.sleep(0.1)
    assert charger.received_calls == []  # the refused command was not queued for later
    assert (await http_request(port, "POST", "/debug/chargers/other/commands", RESET))[1]["error"] == "unknown_charger"
    await charger.close()


async def test_a_disconnect_in_flight_is_a_502(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies, responder=lambda action, payload: None)

    request = asyncio.create_task(http_request(port, "POST", COMMANDS, RESET))
    while not charger.received_calls:
        await asyncio.sleep(0.01)
    await charger.close()

    status, body = await request
    assert (status, body["error"]) == (502, "charger_disconnected")


async def test_a_second_command_while_one_is_in_flight_is_a_409(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies, responder=lambda action, payload: None)  # holds the first command

    first = asyncio.create_task(http_request(port, "POST", COMMANDS, {**RESET, "timeout": 5}))
    while not charger.received_calls:
        await asyncio.sleep(0.01)

    status, body = await http_request(port, "POST", COMMANDS, RESET)
    assert (status, body["error"]) == (409, "busy")

    first.cancel()
    await charger.close()


async def test_chargers_are_listed_with_their_connection_state(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    assert (await http_request(port, "GET", "/debug/chargers"))[1]["chargers"] == [
        {"id": CHARGER_ID, "connected": False, "connected_since": None, "remote": None}
    ]

    charger = await connect(url, proxies)
    (entry,) = (await http_request(port, "GET", "/debug/chargers"))[1]["chargers"]

    assert entry["connected"] and entry["remote"].startswith("127.0.0.1:") and entry["connected_since"]
    await charger.close()


async def test_a_reconnected_charger_gets_the_command_on_its_new_connection(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    first = await connect(url, proxies)
    old = proxies[-1].session_for(CHARGER_ID)
    second = await FakeCharger.connect(url, CHARGER_ID)
    while proxies[-1].session_for(CHARGER_ID) in (None, old):
        await asyncio.sleep(0.01)

    assert (await http_request(port, "POST", COMMANDS, RESET))[0] == 200

    assert len(second.received_calls) == 1 and first.received_calls == []
    await second.close()
    await first.close()


async def test_browser_style_requests_are_refused(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies)

    status, body = await http_request(port, "POST", COMMANDS, RESET, {"Origin": "http://evil.example"})
    assert (status, body["error"]) == (403, "origin_forbidden")
    assert (await http_request(port, "GET", "/debug/chargers", headers={"Origin": "null"}))[0] == 403
    status, body = await http_request(port, "POST", COMMANDS, RESET, {"Content-Type": "text/plain"})
    assert (status, body["error"]) == (415, "unsupported_media_type")
    assert (await http_request(port, "POST", COMMANDS, b"{nope"))[1]["error"] == "invalid_json"
    assert charger.received_calls == []
    await charger.close()


async def test_actions_a_central_system_cannot_send_are_refused(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies)

    for action in ("Heartbeat", "BootNotification", "Bogus"):
        status, body = await http_request(port, "POST", COMMANDS, {"action": action})
        assert (status, body["error"]) == (400, "invalid_action")
    assert charger.received_calls == []
    await charger.close()


async def test_unknown_paths_and_methods(primary, start_proxy, proxies):
    _, port = await start(primary, start_proxy, proxies)

    assert (await http_request(port, "GET", "/nothing"))[0] == 404
    assert (await http_request(port, "GET", COMMANDS))[0] == 405
    assert (await http_request(port, "POST", "/debug/chargers", {}))[0] == 405


async def test_the_endpoint_is_off_unless_configured(primary, start_proxy, proxies):
    await start_proxy(primary.url)

    assert proxies[-1].debug_port is None


async def test_a_debug_command_does_not_disturb_a_backend_command_in_flight(primary, start_proxy, proxies):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies, responder=lambda action, payload: {"echo": action})
    await primary.connected.wait()

    backend, debug = await asyncio.gather(
        primary.call("GetConfiguration", {}, message_id="same-id"),
        http_request(port, "POST", COMMANDS, {"action": "GetConfiguration"}),
    )

    assert backend == [3, "same-id", {"echo": "GetConfiguration"}]
    assert debug[0] == 200 and debug[1]["result"] == {"echo": "GetConfiguration"}
    await charger.close()


async def test_each_command_is_audited_without_its_payload(primary, start_proxy, proxies, caplog):
    url, port = await start(primary, start_proxy, proxies)
    charger = await connect(url, proxies)

    with caplog.at_level(logging.INFO, logger="ocpp_2w_proxy.debug_commands"):
        await http_request(port, "POST", COMMANDS, {"action": "ChangeConfiguration", "payload": {"key": "SECRET-KEY"}})

    assert any("ChangeConfiguration" in r.getMessage() and CHARGER_ID in r.getMessage() for r in caplog.records)
    assert "SECRET-KEY" not in caplog.text
    await charger.close()
