from ocpp_2w_proxy.log_throttle import LogThrottle


def test_ready_at_most_once_per_interval():
    now = [100.0]
    throttle = LogThrottle(60.0, clock=lambda: now[0])

    first = throttle.ready()
    now[0] = 159.9
    within = throttle.ready()
    now[0] = 160.0
    after = throttle.ready()

    assert (first, within, after) == (True, False, True)
