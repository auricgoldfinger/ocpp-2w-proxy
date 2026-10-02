from ocpp_2w_proxy.command_router import CommandRouter
from ocpp_2w_proxy.ocpp import Call, CallResult


class Target:
    def __init__(self, name):
        self.name = name

    async def reply(self, message):  # pragma: no cover - not used here
        pass


def test_router_gives_unique_ids_and_maps_back():
    router = CommandRouter()
    a, b = Target("primary"), Target("secondary")
    to_charger_a = router.outbound(a, Call("1", "GetConfiguration", {}))
    to_charger_b = router.outbound(b, Call("1", "GetConfiguration", {}))
    assert to_charger_a.id != to_charger_b.id != "1"

    routed = router.take(CallResult(to_charger_b.id, {"x": 1}))
    assert routed.target is b and routed.reply == CallResult("1", {"x": 1})
    assert router.take(CallResult(to_charger_b.id, {})) is None


def test_router_expires_stale_routes():
    now = [0.0]
    router = CommandRouter(ttl=10, clock=lambda: now[0])
    stale = router.outbound(Target("p"), Call("1", "Reset", {}))
    now[0] = 11
    router.outbound(Target("p"), Call("2", "Reset", {}))
    assert router.take(CallResult(stale.id, {})) is None
