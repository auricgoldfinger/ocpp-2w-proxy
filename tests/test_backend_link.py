import asyncio

import pytest
from fakes import RecordingFlow

from ocpp_2w_proxy.backend_link import BackendLink
from ocpp_2w_proxy.ocpp import Call, CallError
from ocpp_2w_proxy.traffic_log import TrafficLog


async def _ignore(call):
    pass


@pytest.fixture
async def open_link(secondary):
    """Opens links to the fake backend, each with a reader serving it; closes them all after the test."""
    opened = []

    async def open_(flow=None) -> tuple[BackendLink, asyncio.Task]:
        link = await BackendLink.open("tap", secondary.url, {}, None, flow or TrafficLog("CH1", False))
        reader = asyncio.create_task(link.serve(_ignore))
        opened.append((link, reader))
        return link, reader

    yield open_
    for link, reader in opened:
        reader.cancel()
        await link.close()


async def test_malformed_reply_from_a_backend_fails_the_call_at_once(secondary, open_link):
    secondary.responder = lambda action, payload: ["not", "an", "object"]
    link, _ = await open_link()

    reply = await link.call(Call("1", "Heartbeat", {}), timeout=3)  # not a 3 s wait

    assert isinstance(reply, CallError)
    assert reply.code == "GenericError"
    assert not any(frame[0] == 4 for frame in secondary.replies)  # a reply is not answered


async def test_the_link_reports_coming_up_when_told_and_going_down_with_the_reason(secondary, open_link):
    flow = RecordingFlow()
    link, reader = await open_link(flow)
    assert flow.links == []  # its channel says when it is usable
    link.report_up("boot replayed")
    assert link.connected_since > 0
    await link.call(Call("1", "Heartbeat", {}), timeout=3)
    await secondary.drop_connection()
    await reader
    (_, _, up_note), (_, down, down_note) = flow.link_notes
    assert (up_note, down) == ("boot replayed", False)
    assert down_note.startswith("closed by tap: 1000 (OK) · up ")
    assert "· last frame " in down_note


@pytest.fixture
def reason_after(secondary, open_link):
    """The reason a link gives for going down after `end_link(link, csms, reader)`."""

    async def run(end_link) -> str:
        link, reader = await open_link()
        await secondary.connected.wait()
        await end_link(link, secondary, reader)
        await reader
        return link.disconnect_reason

    return run


async def test_a_link_the_proxy_closes_names_its_cause(reason_after):
    async def close_idle(link, csms, reader):
        await link.close("idle: no session and nothing queued")

    assert await reason_after(close_idle) == "closed by proxy: idle: no session and nothing queued"


async def test_a_link_lost_without_a_close_frame_says_so(reason_after):
    async def abort(link, csms, reader):
        csms.connection.transport.abort()

    assert await reason_after(abort) == "dropped without close frame"


async def test_the_first_cause_wins_and_a_dropped_link_keeps_its_reason(reason_after):
    async def drop_then_mark(link, csms, reader):
        await csms.drop_connection()
        await reader
        link.mark_closing("idle: no session and nothing queued")

    assert (await reason_after(drop_then_mark)).startswith("closed by tap: 1000")

    async def mark_twice(link, csms, reader):
        link.mark_closing("queued StopTransaction not answered in time")
        await link.close("proxy shutting down")

    assert await reason_after(mark_twice) == "closed by proxy: queued StopTransaction not answered in time"
