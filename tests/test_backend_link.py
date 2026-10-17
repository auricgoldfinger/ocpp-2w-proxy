import asyncio

from fakes import FakeCsms, RecordingFlow

from ocpp_2w_proxy.backend_link import BackendLink
from ocpp_2w_proxy.ocpp import Call, CallError
from ocpp_2w_proxy.traffic_log import TrafficLog


async def test_malformed_reply_from_a_backend_fails_the_call_at_once():
    csms = await FakeCsms(lambda action, payload: ["not", "an", "object"]).start()
    link = await BackendLink.open("tap", csms.url, {}, None, TrafficLog("CH1", False))
    reader = asyncio.create_task(link.serve(_ignore))
    try:
        reply = await link.call(Call("1", "Heartbeat", {}), timeout=3)  # not a 3 s wait

        assert isinstance(reply, CallError)
        assert reply.code == "GenericError"
        assert not any(frame[0] == 4 for frame in csms.replies)  # a reply is not answered
    finally:
        reader.cancel()
        await link.close()
        await csms.stop()


async def _ignore(call):
    pass


async def test_the_link_reports_coming_up_when_told_and_going_down_with_the_reason():
    csms = await FakeCsms().start()
    flow = RecordingFlow()
    link = await BackendLink.open("tap", csms.url, {}, None, flow)
    reader = asyncio.create_task(link.serve(_ignore))
    try:
        assert flow.links == []  # its channel says when it is usable
        link.report_up("boot replayed")
        assert link.connected_since > 0
        await link.call(Call("1", "Heartbeat", {}), timeout=3)
        await csms.drop_connection()
        await reader
        (_, _, up_note), (_, down, down_note) = flow.link_notes
        assert (up_note, down) == ("boot replayed", False)
        assert down_note.startswith("closed by tap: 1000 (OK) · up ")
        assert "· last frame " in down_note
    finally:
        await link.close()
        await csms.stop()


async def _reason_after(end_link) -> str:
    csms = await FakeCsms().start()
    flow = RecordingFlow()
    link = await BackendLink.open("tap", csms.url, {}, None, flow)
    reader = asyncio.create_task(link.serve(_ignore))
    try:
        await csms.connected.wait()
        await end_link(link, csms, reader)
        await reader
        return link.disconnect_reason
    finally:
        await link.close()
        await csms.stop()


async def test_a_link_the_proxy_closes_names_its_cause():
    async def close_idle(link, csms, reader):
        await link.close("idle: no session and nothing queued")

    assert await _reason_after(close_idle) == "closed by proxy: idle: no session and nothing queued"


async def test_a_link_lost_without_a_close_frame_says_so():
    async def abort(link, csms, reader):
        csms.connection.transport.abort()

    assert await _reason_after(abort) == "dropped without close frame"


async def test_the_first_cause_wins_and_a_dropped_link_keeps_its_reason():
    async def drop_then_mark(link, csms, reader):
        await csms.drop_connection()
        await reader
        link.mark_closing("idle: no session and nothing queued")

    assert (await _reason_after(drop_then_mark)).startswith("closed by tap: 1000")

    async def mark_twice(link, csms, reader):
        link.mark_closing("queued StopTransaction not answered in time")
        await link.close("proxy shutting down")

    assert await _reason_after(mark_twice) == "closed by proxy: queued StopTransaction not answered in time"
