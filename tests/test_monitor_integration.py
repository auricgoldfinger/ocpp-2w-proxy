"""The Flow Monitor inside a running proxy: real chargers and backends (fakes), read back over its HTTP API."""

from __future__ import annotations

from conftest import CHARGER_ID
from fakes import http_exchange

from ocpp_2w_proxy.flow import FanOut
from ocpp_2w_proxy.traffic_log import TrafficLog


async def test_without_the_monitor_the_flow_is_the_plain_traffic_log(start_proxy, proxies, primary):
    await start_proxy(primary.url)

    proxy = proxies[0]
    assert type(proxy.context_for(CHARGER_ID).traffic) is TrafficLog
    assert proxy.monitor_port is None


async def test_with_the_monitor_the_flow_fans_out_and_the_ui_is_served(start_proxy, proxies, primary):
    await start_proxy(primary.url, monitor={})

    proxy = proxies[0]
    assert isinstance(proxy.context_for(CHARGER_ID).traffic, FanOut)
    reply = await http_exchange(proxy.monitor_port, "GET", "/api/config")
    assert (reply.status, reply.json()["chargers"]) == (200, [CHARGER_ID])
