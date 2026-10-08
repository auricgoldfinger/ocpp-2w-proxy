import pytest

from ocpp_2w_proxy.message_classes import MessageClass, classify, latest_key
from ocpp_2w_proxy.ocpp import Call


@pytest.mark.parametrize(
    ("action", "payload", "expected"),
    [
        ("StopTransaction", {"transactionId": 1}, MessageClass.DURABLE),
        ("MeterValues", {"connectorId": 1, "transactionId": 7}, MessageClass.DURABLE),
        ("MeterValues", {"connectorId": 1}, MessageClass.DROPPABLE),
        ("Heartbeat", {}, MessageClass.DROPPABLE),
        ("StatusNotification", {"connectorId": 1}, MessageClass.LATEST),
        ("FirmwareStatusNotification", {"status": "Installed"}, MessageClass.LATEST),
        ("DiagnosticsStatusNotification", {"status": "Uploaded"}, MessageClass.LATEST),
        ("Authorize", {"idTag": "x"}, MessageClass.LIVE),
        ("StartTransaction", {"connectorId": 1}, MessageClass.LIVE),
        ("BootNotification", {}, MessageClass.LIVE),
        ("DataTransfer", {}, MessageClass.LIVE),
    ],
)
def test_classification(action, payload, expected):
    assert classify(Call("1", action, payload)) is expected


def test_latest_key_is_per_connector_and_action():
    one = latest_key(Call("1", "StatusNotification", {"connectorId": 1}))
    two = latest_key(Call("2", "StatusNotification", {"connectorId": 2}))
    again = latest_key(Call("3", "StatusNotification", {"connectorId": 1}))
    assert one == again != two
    assert latest_key(Call("4", "FirmwareStatusNotification", {})) != one
