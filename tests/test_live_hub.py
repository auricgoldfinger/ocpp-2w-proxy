from __future__ import annotations

import asyncio

import pytest

from ocpp_2w_proxy import minihttp
from ocpp_2w_proxy.minihttp import EventStream, Request
from ocpp_2w_proxy.monitor.live_hub import RESET, LiveHub, Subscription, TooManySubscribers, sse_frame


async def drain(subscription: Subscription) -> list[bytes]:
    return [frame async for frame in subscription]


def test_a_frame_is_an_sse_event_with_one_data_line_per_line():
    assert sse_frame("exchange", b'{"id": "1"}') == b'event: exchange\ndata: {"id": "1"}\n\n'
    assert sse_frame("x", b"a\nb") == b"event: x\ndata: a\ndata: b\n\n"
    assert RESET == b"event: reset\ndata: \n\n"


async def test_every_subscriber_gets_the_same_frame_bytes_object():
    hub = LiveHub()
    first, second = hub.subscribe(), hub.subscribe()

    hub.publish("exchange", b"{}")

    assert (await anext(first)) is (await anext(second))


async def test_a_slow_subscriber_is_reset_while_the_others_continue():
    hub = LiveHub(queue_size=3)
    slow, fast = hub.subscribe(), hub.subscribe()

    for n in range(5):
        hub.publish("exchange", b"%d" % n)
        assert await anext(fast) == sse_frame("exchange", b"%d" % n)

    assert await drain(slow) == [RESET]
    hub.publish("exchange", b"after")
    assert await anext(fast) == sse_frame("exchange", b"after")


async def test_a_reset_subscriber_frees_its_slot():
    hub = LiveHub(max_subscribers=1, queue_size=1)
    hub.subscribe()
    hub.publish("exchange", b"1")
    hub.publish("exchange", b"2")  # overflows

    hub.subscribe()  # does not raise


async def test_a_fifth_subscriber_is_refused_until_a_slot_frees():
    hub = LiveHub()
    subscriptions = [hub.subscribe() for _ in range(4)]

    with pytest.raises(TooManySubscribers):
        hub.subscribe()

    await subscriptions[0].aclose()
    hub.subscribe()
    assert await drain(subscriptions[0]) == []


async def test_a_cancelled_read_frees_the_slot():
    hub = LiveHub(max_subscribers=1)
    reading = asyncio.create_task(anext(hub.subscribe()))
    await asyncio.sleep(0)

    reading.cancel()
    with pytest.raises(asyncio.CancelledError):
        await reading

    hub.subscribe()


async def test_close_ends_every_stream_and_later_ones_at_once():
    hub = LiveHub()
    first, second = hub.subscribe(), hub.subscribe()
    hub.publish("exchange", b"pending")

    hub.close()

    assert await drain(first) == []
    assert await drain(second) == []
    assert await drain(hub.subscribe()) == []


def test_publishing_without_subscribers_does_nothing():
    LiveHub().publish("exchange", b"{}")


async def test_a_client_leaving_an_event_stream_gives_its_slot_back():
    hub = LiveHub(max_subscribers=1)

    async def handler(request: Request) -> EventStream:
        return EventStream(hub.subscribe())

    server = await minihttp.serve(handler, "127.0.0.1", 0)
    reader, writer = await asyncio.open_connection("127.0.0.1", server.sockets[0].getsockname()[1])
    writer.write(b"GET /api/events HTTP/1.1\r\n\r\n")
    await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5)
    hub.publish("exchange", b"{}")
    assert await asyncio.wait_for(reader.readuntil(b"\n\n"), 5) == sse_frame("exchange", b"{}")

    writer.close()
    for _ in range(100):
        await asyncio.sleep(0.01)
        try:
            hub.subscribe()
            break
        except TooManySubscribers:
            continue
    else:
        pytest.fail("the slot was not given back")

    server.close()
    await server.wait_closed()
