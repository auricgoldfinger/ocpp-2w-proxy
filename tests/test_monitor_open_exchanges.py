from monitor_events import received, sent

from ocpp_2w_proxy.monitor.gaps import ExchangeSpan
from ocpp_2w_proxy.monitor.open_exchanges import OpenExchanges, TrackedExchange
from ocpp_2w_proxy.monitor.projection import ExchangeProjection
from ocpp_2w_proxy.ocpp import Call, CallResult

MV = Call("w1", "MeterValues", {"connectorId": 1})


def tracked(exchange_id, *, settled):
    projection = ExchangeProjection(exchange_id)
    projection.apply(received(1.0, "charger", MV))
    projection.apply(sent(1.1, "primary", MV))
    if settled:
        projection.apply(received(1.2, "primary", CallResult("w1", {})))
    return TrackedExchange(projection)


def ids(exchanges):
    return sorted(t.exchange_id for t in exchanges)


def test_settled_exchanges_go_after_the_grace_open_ones_after_the_idle_time():
    table = OpenExchanges(settled_grace=10.0, open_idle=100.0)
    table.touch(tracked(1, settled=False), now=0.0)
    table.touch(tracked(2, settled=True), now=5.0)

    table.evict(now=14.9)
    assert ids(table) == [1, 2]
    table.evict(now=15.0)
    assert ids(table) == [1]
    table.evict(now=100.0)
    assert ids(table) == []


def test_a_settled_exchange_is_not_held_back_by_an_older_open_one():
    table = OpenExchanges(settled_grace=10.0, open_idle=100.0)
    table.touch(tracked(1, settled=False), now=0.0)
    table.touch(tracked(2, settled=True), now=1.0)

    table.evict(now=20.0)

    assert ids(table) == [1]
    assert 2 not in table and table.get(2) is None


def test_touch_restarts_the_idle_time_and_follows_settling():
    table = OpenExchanges(settled_grace=10.0, open_idle=100.0)
    exchange = tracked(1, settled=False)
    table.touch(exchange, now=0.0)
    exchange.projection.apply(received(2.0, "primary", CallResult("w1", {})))
    table.touch(exchange, now=50.0)

    table.evict(now=59.0)
    assert table.get(1) is exchange
    table.evict(now=60.0)
    assert len(table) == 0


def test_capacity_evicts_the_least_recently_touched():
    table = OpenExchanges(max_exchanges=2)
    table.touch(tracked(1, settled=True), now=0.0)
    table.touch(tracked(2, settled=False), now=1.0)
    table.touch(tracked(3, settled=True), now=2.0)

    assert ids(table) == [2, 3]


def test_span_reports_open_and_orphan():
    exchange = tracked(7, settled=False)
    orphan = ExchangeProjection(8)
    orphan.apply(received(3.0, "primary", CallResult("zz", {})))

    assert exchange.span() == ExchangeSpan(7, "cp", 1.0, 1.1, open=True, orphan=False)
    assert TrackedExchange(orphan).span() == ExchangeSpan(8, "cp", 3.0, 3.0, open=False, orphan=True)
