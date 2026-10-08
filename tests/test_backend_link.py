import asyncio

from fakes import FakeCsms

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
