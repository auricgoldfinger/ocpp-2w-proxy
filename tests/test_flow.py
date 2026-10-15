from ocpp_2w_proxy.flow import FanOut
from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.reasons import Outcome, Reason


class Recording:
    def __init__(self):
        self.calls = []

    def received(self, peer, message):
        self.calls.append(("received", peer, message))

    def sent(self, peer, message):
        self.calls.append(("sent", peer, message))

    def decided(self, peer, call, reason, detail=None):
        self.calls.append(("decided", peer, call, reason, detail))

    def link_changed(self, peer, up):
        self.calls.append(("link_changed", peer, up))


class Raising:
    def __getattr__(self, name):
        def fail(*args):
            raise RuntimeError("monitor broke")

        return fail


CALL = Call("1", "Heartbeat", {})


def test_fan_out_reports_to_log_and_monitor():
    log, monitor = Recording(), Recording()
    flow = FanOut(log, monitor)

    flow.received("charger", CALL)
    flow.sent("primary", CallResult("1", {}))
    flow.decided("stats", CALL, Reason.QUEUED, 7)
    flow.link_changed("stats", False)

    assert log.calls == monitor.calls
    assert [entry[0] for entry in log.calls] == ["received", "sent", "decided", "link_changed"]


def test_failing_monitor_never_reaches_the_relay():
    log = Recording()
    flow = FanOut(log, Raising())

    flow.received("charger", CALL)
    flow.sent("primary", CALL)
    flow.decided("stats", CALL, Reason.QUEUED)
    flow.link_changed("stats", True)

    assert len(log.calls) == 4
    assert flow.failures == 4


def test_every_reason_has_an_outcome_and_renders():
    for reason in Reason:
        assert isinstance(reason.outcome, Outcome)
        assert "{" not in reason.describe("stats", 42)


def test_problem_reasons():
    assert Reason.CHARGER_NO_REPLY.is_problem
    assert Reason.QUEUE_OVERFLOW.is_problem
    assert not Reason.SUPERSEDED.is_problem
    assert not Reason.POLICY_REFUSED.is_problem
    assert not Reason.QUEUED.is_problem
