import pytest

from ocpp_2w_proxy.command_router import CommandRouter
from ocpp_2w_proxy.debug_target import ChargerGone, FutureReplyTarget
from ocpp_2w_proxy.ocpp import Call, CallError, CallResult


async def test_the_reply_resolves_the_waiter():
    target = FutureReplyTarget()
    await target.reply(CallResult("1", {"status": "Accepted"}))
    assert await target.wait(1) == CallResult("1", {"status": "Accepted"})


async def test_the_first_outcome_wins():
    target = FutureReplyTarget()
    await target.reply(CallError("1", "NotSupported"))
    await target.reply(CallResult("1", {}))
    target.fail(ChargerGone())
    assert await target.wait(1) == CallError("1", "NotSupported")


async def test_a_failure_is_raised_to_the_waiter():
    target = FutureReplyTarget()
    target.fail(ChargerGone())
    with pytest.raises(ChargerGone):
        await target.wait(1)


async def test_no_reply_times_out():
    with pytest.raises(TimeoutError):
        await FutureReplyTarget().wait(0.01)


async def test_the_router_hands_the_charger_reply_to_the_target():
    router, target = CommandRouter(), FutureReplyTarget()
    sent = router.outbound(target, Call("mine", "GetConfiguration", {}))

    routed = router.take(CallResult(sent.id, {"configurationKey": []}))
    await routed.target.reply(routed.reply)

    assert await target.wait(1) == CallResult("mine", {"configurationKey": []})
