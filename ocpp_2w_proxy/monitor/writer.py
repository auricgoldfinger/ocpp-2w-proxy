"""The monitor's one writer task: from the recorders' queue to the store and the live stream.

It takes a batch (up to 500 events, or whatever arrived within 200 ms of the first), and works through it in
slices of about 0.1 ms (at most 50 events), resting after each so it never takes more than a quarter of the
loop (see ``loop_slice``): a relay round trip meets one slice at most. The assembler does the per-event work;
compression and SQLite run in the write worker's thread. The writer also counts the frames per peer (``rates``),
so the relay path never has to.

A failing database costs data, never the writer: the batch is counted in ``dropped_batches`` and the writer
carries on with the next one.
"""

from __future__ import annotations

import asyncio
import logging
import sqlite3
import time
from collections.abc import Callable, Iterable, Sequence
from contextlib import suppress

from ..log_throttle import LogThrottle
from .batch_assembler import BatchAssembler, Routed
from .compact_json import dumps_bytes
from .db import write_batch
from .events import EventKind
from .gaps import GapDetector
from .live_hub import LiveHub
from .loop_slice import SLICE_BUDGET, SLICE_MAX_ITEMS, LoopSlice
from .message_rates import MessageRates
from .not_sent import NotSentMap
from .open_exchanges import TrackedExchange
from .queries import rebuild_projections
from .recorder_hub import RecorderHub
from .rows import WriteBatch
from .sqlite_worker import SqliteWorker

logger = logging.getLogger(__name__)

BATCH_SIZE = 500
BATCH_WINDOW = 0.2
POLL_INTERVAL = 0.02
FLUSH_TIMEOUT = 1.5
_FRAME_KINDS = frozenset({EventKind.RECEIVED, EventKind.SENT})

type Clock = Callable[[], float]


class MonitorWriter:
    def __init__(
        self,
        recorders: RecorderHub,
        db: SqliteWorker,
        live: LiveHub,
        not_sent: NotSentMap,
        *,
        batch_size: int = BATCH_SIZE,
        batch_window: float = BATCH_WINDOW,
        slice_size: int = SLICE_MAX_ITEMS,
        slice_budget: float = SLICE_BUDGET,
        flush_timeout: float = FLUSH_TIMEOUT,
        clock: Clock = time.time,
    ):
        self._recorders = recorders
        self._queue = recorders.queue
        self._db = db
        self._live = live
        self._batch_size = batch_size
        self._batch_window = batch_window
        self._slice_size = slice_size
        self._slice_budget = slice_budget
        self._slice = LoopSlice(slice_budget, slice_size)
        self._flush_timeout = flush_timeout
        self._clock = clock
        self._assembler = BatchAssembler(not_sent)
        self._gaps = GapDetector(clock())
        self._error_log = LogThrottle()
        self._task: asyncio.Task | None = None
        self._waiting = False
        self._closing = False
        self.dropped_batches = 0
        self.rates = MessageRates()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="monitor-writer")

    async def close(self) -> None:
        """Stop taking new batches, then flush what is still queued; gives up after ``flush_timeout``."""
        self._closing = True
        task = self._task
        if task is not None:
            if self._waiting:
                task.cancel()  # idle in queue.get(): no event is lost, it stays in the queue
            await asyncio.wait([task])
        # Nothing is relayed any more: the flush still yields to the loop, but never rests.
        self._slice = LoopSlice(self._slice_budget, self._slice_size, max_share=1.0)
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._flush_timeout
        while not self._queue.empty() and loop.time() < deadline:
            batch: list[tuple] = []
            self._drain_into(batch)
            await self._process(batch)

    # --- batching -----------------------------------------------------------------------------

    async def _run(self) -> None:
        while not self._closing:
            self._waiting = True
            try:
                first = await self._queue.get()
            finally:
                self._waiting = False
            await self._process(await self._collect(first))

    async def _collect(self, first: tuple) -> list[tuple]:
        """The first event plus what follows, until the batch is full or the window has passed. No wait per event."""
        batch = [first]
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._batch_window
        while True:
            self._drain_into(batch)
            remaining = deadline - loop.time()
            if len(batch) >= self._batch_size or remaining <= 0 or self._closing:
                return batch
            await asyncio.sleep(min(POLL_INTERVAL, remaining))

    def _drain_into(self, batch: list[tuple]) -> None:
        with suppress(asyncio.QueueEmpty):
            while len(batch) < self._batch_size:
                batch.append(self._queue.get_nowait())

    # --- one batch ----------------------------------------------------------------------------

    async def _process(self, raw_events: Sequence[tuple]) -> None:
        assembler = self._assembler
        self._slice.restart()
        try:
            assembler.evict(self._clock())
            routed = []
            await self._sliced(raw_events, lambda raw: routed.append(self._correlate(raw)))
            unknown = assembler.take_unknown()
            if unknown:
                assembler.adopt(await self._rebuild(unknown), self._clock())
                self._slice.restart()
            await self._sliced(routed, assembler.fold)
            await self._observe_gaps()
            exchange_frames: list[bytes] = []
            if self._live.has_subscribers:
                live_frame = self._live_frame
                await self._sliced(assembler.changed(), lambda changed: exchange_frames.append(live_frame(changed)))
            else:
                await self._sliced(assembler.changed(), assembler.record)
            batch = assembler.take_batch()
        except Exception:
            assembler.take_batch()  # start the next batch clean
            self._dropped_batch("monitor writer failed on a batch")
            return
        await self._write(batch)
        self._slice.restart()
        await self._publish(batch, exchange_frames)

    def _correlate(self, raw: tuple) -> Routed:
        kind, at, charger_id, peer = raw[:4]
        if kind in _FRAME_KINDS:
            self.rates.count(charger_id, peer, at)
        return self._assembler.correlate(raw)

    def _live_frame(self, tracked: TrackedExchange) -> bytes:
        return dumps_bytes(self._assembler.summarize(tracked))

    async def _sliced[T](self, items: Iterable[T], step: Callable[[T], object]) -> None:
        """``step`` every item, yielding to the loop whenever the current slice is spent."""
        loop_slice = self._slice
        for item in items:
            step(item)
            if loop_slice.spent():
                await loop_slice.yield_to_loop()

    async def _rebuild(self, exchange_ids: set[int]) -> list:
        """Exchanges continued after a restart or eviction, projected again from their stored rows."""
        try:
            return await self._db.run(rebuild_projections, exchange_ids)
        except sqlite3.Error:
            self._failed("monitor store could not be read; continued exchanges restart from their new events")
            return []

    async def _observe_gaps(self) -> None:
        now = self._clock()
        gaps = self._gaps.observe(self._recorders.dropped(), self._recorders.last_drop_at(), now)
        if not gaps:
            return
        self._assembler.record_gaps(gaps, now)
        for chunk in _chunks(self._assembler.tracked(), self._slice_size):
            self._assembler.mark_incomplete(gaps, chunk)
            await self._slice.yield_to_loop()

    async def _write(self, batch: WriteBatch) -> None:
        try:
            await self._db.run(write_batch, batch)
        except Exception:  # sqlite3.Error above all; nothing may stop the writer
            self._dropped_batch("monitor store write failed")

    async def _publish(self, batch: WriteBatch, exchange_frames: Sequence[bytes]) -> None:
        for gap in batch.gaps:
            self._live.publish("gap", dumps_bytes(gap._asdict()))
        for link in batch.links:
            self._live.publish("link", dumps_bytes(link._asdict()))
        await self._sliced(exchange_frames, lambda data: self._live.publish("exchange", data))

    def _dropped_batch(self, what: str) -> None:
        self.dropped_batches += 1
        self._failed(f"{what}; its events are lost ({self.dropped_batches} batches dropped so far)")

    def _failed(self, message: str) -> None:
        if self._error_log.ready():
            logger.exception(message)


def _chunks[T](items: Sequence[T], size: int) -> Iterable[Sequence[T]]:
    return (items[start : start + size] for start in range(0, len(items), size))
