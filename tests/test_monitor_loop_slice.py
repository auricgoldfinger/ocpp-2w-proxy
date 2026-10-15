import asyncio

import pytest

from ocpp_2w_proxy.monitor import loop_slice
from ocpp_2w_proxy.monitor.loop_slice import LoopSlice


def items_until_spent(loop_slice: LoopSlice, limit: int = 1000) -> int:
    for count in range(1, limit + 1):
        if loop_slice.spent():
            return count
    return limit


def test_a_slice_ends_at_its_item_cap_while_time_remains():
    assert items_until_spent(LoopSlice(budget=60.0, max_items=7)) == 7


def test_a_slice_ends_when_its_time_budget_is_spent(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(loop_slice, "_clock", lambda: now[0])
    sliced = LoopSlice(budget=0.001, max_items=50)

    assert sliced.spent() is False
    now[0] += 0.001
    assert sliced.spent() is True


async def test_after_a_slice_it_rests_long_enough_to_keep_its_loop_share(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(loop_slice, "_clock", lambda: now[0])
    rests = []

    async def sleep(seconds):
        rests.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", sleep)
    sliced = LoopSlice(budget=0.001, max_items=50, max_share=0.25)
    now[0] += 0.0001

    await sliced.yield_to_loop()

    assert rests == [pytest.approx(0.0003)]


async def test_a_full_share_only_yields():
    sliced = LoopSlice(budget=0.001, max_items=50, max_share=1.0)
    ran = []
    asyncio.get_running_loop().call_soon(ran.append, "other")

    await sliced.yield_to_loop()

    assert ran == ["other"]


async def test_yielding_starts_a_new_slice():
    sliced = LoopSlice(budget=60.0, max_items=3)
    items_until_spent(sliced)
    ran = []
    asyncio.get_running_loop().call_soon(ran.append, "other")

    await sliced.yield_to_loop()

    assert ran == ["other"]
    assert items_until_spent(sliced) == 3
