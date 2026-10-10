import asyncio
from dataclasses import dataclass

import pytest

from ocpp_2w_proxy.debug_commands import DebugCommands, DebugError
from ocpp_2w_proxy.debug_config import DebugConfig
from ocpp_2w_proxy.debug_target import ChargerGone, CommandTimeout
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult


@dataclass
class FakeOutcome:
    sent: Call
    reply: CallResult | CallError
    latency_ms: int = 7


class FakeSession:
    connected_since = "2026-01-01T00:00:00Z"
    remote = "10.0.0.5:4242"

    def __init__(self, reply=None, error=None):
        self.reply = reply or CallResult("wire-1", {"status": "Accepted"})
        self.error = error
        self.timeouts: list[float] = []
        self.release = asyncio.Event()
        self.release.set()

    async def send_command(self, action, payload, timeout):
        self.timeouts.append(timeout)
        await self.release.wait()
        if self.error:
            raise self.error
        return FakeOutcome(Call("wire-1", action, payload), self.reply)


class FakeDirectory:
    def __init__(self, sessions):
        self.sessions = sessions

    def session_for(self, charger_id):
        return self.sessions.get(charger_id)

    def charger_ids(self):
        return ("CH1", "CH2")


def commands(session=None, **config):
    return DebugCommands(FakeDirectory({"CH1": session or FakeSession()}), DebugConfig(**config))


async def refused(coroutine) -> DebugError:
    with pytest.raises(DebugError) as caught:
        await coroutine
    return caught.value


def test_the_chargers_are_listed_with_their_connection():
    listing = commands().chargers()["chargers"]
    assert listing == [
        {"id": "CH1", "connected": True, "connected_since": "2026-01-01T00:00:00Z", "remote": "10.0.0.5:4242"},
        {"id": "CH2", "connected": False, "connected_since": None, "remote": None},
    ]


async def test_a_call_result_is_described_with_both_frames():
    body = await commands().send("CH1", {"action": "Reset", "payload": {"type": "Soft"}})

    assert body == {
        "status": "CallResult",
        "result": {"status": "Accepted"},
        "answered_by": "charger",
        "message_id": "wire-1",
        "action": "Reset",
        "latency_ms": 7,
        "sent": [2, "wire-1", "Reset", {"type": "Soft"}],
        "received": [3, "wire-1", {"status": "Accepted"}],
    }


async def test_a_call_error_is_described_as_an_error():
    session = FakeSession(reply=CallError("wire-1", "NotSupported", "nope", {"a": 1}))

    body = await commands(session).send("CH1", {"action": "Reset"})

    assert body["status"] == "CallError"
    assert body["error"] == {"code": "NotSupported", "description": "nope", "details": {"a": 1}}
    assert body["received"] == [4, "wire-1", "NotSupported", "nope", {"a": 1}]
    assert "result" not in body


@pytest.mark.parametrize(
    "body",
    [
        [],
        {},
        {"action": 5},
        {"action": "Heartbeat"},  # charger -> central system
        {"action": "Nope"},
        {"action": "Reset", "payload": []},
        {"action": "Reset", "timeout": "5"},
        {"action": "Reset", "timeout": 0},
        {"action": "Reset", "timeout": True},
    ],
)
async def test_an_invalid_request_is_a_400(body):
    assert (await refused(commands().send("CH1", body))).status == 400


async def test_unknown_and_offline_chargers_are_404():
    assert (await refused(commands().send("nope", {"action": "Reset"}))).code == "unknown_charger"
    assert (await refused(commands().send("CH2", {"action": "Reset"}))).code == "not_connected"


async def test_the_timeout_defaults_and_is_clamped():
    session = FakeSession()
    debug = commands(session, default_timeout=12, max_timeout=40)

    await debug.send("CH1", {"action": "Reset"})
    await debug.send("CH1", {"action": "Reset", "timeout": 5})
    await debug.send("CH1", {"action": "Reset", "timeout": 999})

    assert session.timeouts == [12, 5, 40]


async def test_a_second_command_while_one_is_in_flight_is_a_409_and_the_lock_is_released():
    session = FakeSession()
    session.release.clear()
    debug = commands(session)
    first = asyncio.create_task(debug.send("CH1", {"action": "Reset"}))
    await asyncio.sleep(0)

    assert (await refused(debug.send("CH1", {"action": "Reset"}))).status == 409
    session.release.set()
    await first
    await debug.send("CH1", {"action": "Reset"})


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [(ChargerGone(), 502, "charger_disconnected"), (CommandTimeout(Call("w", "Reset", {})), 504, "charger_timeout")],
)
async def test_a_failed_send_maps_to_its_status_and_frees_the_lock(error, status, code):
    debug = commands(FakeSession(error=error))

    failure = await refused(debug.send("CH1", {"action": "Reset"}))

    assert (failure.status, failure.code) == (status, code)
    assert (await refused(debug.send("CH1", {"action": "Reset"}))).status == status  # not 409


async def test_a_timeout_carries_the_frame_that_was_sent():
    failure = await refused(
        commands(FakeSession(error=CommandTimeout(Call("w", "Reset", {"type": "Soft"})))).send(
            "CH1", {"action": "Reset"}
        )
    )
    assert failure.extra["sent"] == [2, "w", "Reset", {"type": "Soft"}]
