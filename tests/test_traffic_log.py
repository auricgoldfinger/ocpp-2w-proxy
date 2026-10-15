import logging

from ocpp_2w_proxy.ocpp import Call, CallResult
from ocpp_2w_proxy.reasons import Reason
from ocpp_2w_proxy.traffic_log import TrafficLog


def test_frames_name_both_ends_aligned_across_all_peers(caplog):
    traffic = TrafficLog("CH1", False, ["primary", "homeassistant"])

    with caplog.at_level(logging.INFO, logger="ocpp_2w_proxy.traffic"):
        traffic.received("charger", Call("1", "Heartbeat", {}))
        traffic.sent("primary", Call("1", "Heartbeat", {}))
        traffic.received("homeassistant", CallResult("2", {}))

    lines = [record.getMessage() for record in caplog.records]
    assert lines[0].startswith("CH1       charger -> proxy         Call Heartbeat")
    assert lines[1].startswith("CH1         proxy -> primary       Call Heartbeat")
    assert lines[2].startswith("CH1 homeassistant -> proxy         CallResult")


def test_decisions_and_link_changes_add_no_log_lines(caplog):
    traffic = TrafficLog("CH1", True, ["primary"])

    with caplog.at_level(logging.DEBUG, logger="ocpp_2w_proxy.traffic"):
        traffic.decided("primary", Call("1", "MeterValues", {}), Reason.QUEUED)
        traffic.link_changed("primary", True)

    assert caplog.records == []
