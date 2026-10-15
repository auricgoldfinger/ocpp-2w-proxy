import logging

import pytest

from ocpp_2w_proxy.listen_address import is_loopback, warn_unless_loopback


@pytest.mark.parametrize(
    ("host", "loopback"),
    [
        ("localhost", True),
        ("127.0.0.1", True),
        ("127.1.2.3", True),
        ("::1", True),
        ("0.0.0.0", False),
        ("192.168.1.10", False),
        ("::", False),
        ("example.org", False),
    ],
)
def test_only_loopback_addresses_stay_on_this_host(host, loopback):
    assert is_loopback(host) is loopback


def test_a_loopback_listener_is_not_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        warn_unless_loopback(logging.getLogger("t"), "127.0.0.1", "debug endpoint", "anything goes")
    assert caplog.records == []


def test_a_listener_beyond_this_host_is_warned_about_on_the_callers_logger(caplog):
    with caplog.at_level(logging.WARNING):
        warn_unless_loopback(logging.getLogger("t"), "0.0.0.0", "debug endpoint", "anything goes")
    [record] = caplog.records
    assert record.name == "t"
    assert record.getMessage() == (
        "debug endpoint is reachable beyond this host and has no authentication: anything goes"
    )
