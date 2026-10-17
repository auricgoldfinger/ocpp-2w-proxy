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


@pytest.mark.parametrize(
    ("host", "messages"),
    [
        ("127.0.0.1", []),
        ("0.0.0.0", [("t", "debug endpoint is reachable beyond this host and has no authentication: anything goes")]),
    ],
)
def test_only_a_listener_beyond_this_host_is_warned_about_on_the_callers_logger(caplog, host, messages):
    with caplog.at_level(logging.WARNING):
        warn_unless_loopback(logging.getLogger("t"), host, "debug endpoint", "anything goes")
    assert [(record.name, record.getMessage()) for record in caplog.records] == messages
