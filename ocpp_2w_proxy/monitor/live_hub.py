"""Fan-out of live monitor events to the browsers following them over Server-Sent Events.

Publishing never blocks and never waits for a subscriber: each frame is formatted once, and the same
bytes object goes into every subscriber's bounded queue. A subscriber that falls behind gets a
`reset` event and its stream ends; the browser then re-fetches and subscribes again.
"""

from __future__ import annotations

import asyncio

MAX_SUBSCRIBERS = 4
QUEUE_SIZE = 256


class TooManySubscribers(Exception):
    """Every subscriber slot is taken; the API answers 503."""


def sse_frame(event_name: str, data: bytes) -> bytes:
    lines = b"".join(b"data: " + line + b"\n" for line in data.split(b"\n"))
    return b"event: " + event_name.encode("ascii") + b"\n" + lines + b"\n"


RESET = sse_frame("reset", b"")


class Subscription:
    """One subscriber's stream of SSE frames; an `AsyncIterator[bytes]` for `minihttp.EventStream`.

    It gives its slot back when it ends, when a read is cancelled, or on `aclose()`.
    """

    def __init__(self, hub: LiveHub, queue_size: int):
        self._hub = hub
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(queue_size)

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self._queue.get()
        except asyncio.QueueShutDown:
            self._leave()
            raise StopAsyncIteration from None
        except asyncio.CancelledError:
            self._leave()
            raise

    async def aclose(self) -> None:
        self._leave()
        self.end()

    def offer(self, frame: bytes) -> bool:
        """Queue `frame` without waiting; False when this subscriber has fallen behind."""
        try:
            self._queue.put_nowait(frame)
        except asyncio.QueueFull:
            return False
        return True

    def end(self, last_frame: bytes | None = None) -> None:
        """End the stream at once: frames not yet read are dropped, `last_frame` is still sent."""
        while not self._queue.empty():
            self._queue.get_nowait()
        if last_frame is not None:
            self._queue.put_nowait(last_frame)
        self._queue.shutdown()  # reads drain what is left, then stop

    def _leave(self) -> None:
        self._hub.unsubscribe(self)


class LiveHub:
    def __init__(self, max_subscribers: int = MAX_SUBSCRIBERS, queue_size: int = QUEUE_SIZE):
        self._max_subscribers = max_subscribers
        self._queue_size = queue_size
        self._subscribers: set[Subscription] = set()
        self._closed = False

    def subscribe(self) -> Subscription:
        """A new stream of every frame published from now on; raises TooManySubscribers when full."""
        if len(self._subscribers) >= self._max_subscribers:
            raise TooManySubscribers(f"at most {self._max_subscribers} live subscribers")
        subscription = Subscription(self, self._queue_size)
        if self._closed:
            subscription.end()
        else:
            self._subscribers.add(subscription)
        return subscription

    @property
    def has_subscribers(self) -> bool:
        """Whether anyone follows the stream; without subscribers the writer skips building what it would publish."""
        return bool(self._subscribers)

    def unsubscribe(self, subscription: Subscription) -> None:
        self._subscribers.discard(subscription)

    def publish(self, event_name: str, data: bytes) -> None:
        if not self._subscribers:
            return
        frame = sse_frame(event_name, data)
        for subscription in list(self._subscribers):
            if not subscription.offer(frame):
                self._subscribers.discard(subscription)
                subscription.end(RESET)

    def close(self) -> None:
        self._closed = True
        for subscription in self._subscribers:
            subscription.end()
        self._subscribers.clear()
