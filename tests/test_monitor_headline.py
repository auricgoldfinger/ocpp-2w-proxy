import pytest

from ocpp_2w_proxy.monitor.headline import HEADLINES, headline
from ocpp_2w_proxy.monitor.meter_values import ENERGY, POWER, latest_reading, number


def meter(*sampled, more=()):
    return {"connectorId": 1, "meterValue": [*more, {"timestamp": "t", "sampledValue": list(sampled)}]}


@pytest.mark.parametrize(
    ("action", "payload", "expected"),
    [
        (
            "MeterValues",
            meter(
                {"value": " 7400 ", "measurand": "Power.Active.Import", "unit": "W"},
                {"value": "1234500", "unit": "Wh"},
            ),
            "7.4 kW · 1234.5 kWh",
        ),
        ("MeterValues", meter({"value": "1234.5", "unit": "kWh", "measurand": ENERGY}), "1234.5 kWh"),
        ("MeterValues", meter({"value": "7.4", "measurand": POWER, "unit": "kW"}), "7.4 kW"),
        ("MeterValues", meter({"value": "n/a"}), None),
        ("MeterValues", meter({"value": "5", "measurand": "Temperature", "unit": "Celsius"}), None),
        ("MeterValues", {"meterValue": "garbage"}, None),
        ("MeterValues", {"meterValue": [None, {"sampledValue": None}]}, None),
        ("MeterValues", {}, None),
        ("StatusNotification", {"connectorId": 1, "status": "Charging", "errorCode": "NoError"}, "Charging"),
        ("StatusNotification", {"status": "Faulted", "errorCode": "GroundFailure"}, "Faulted · GroundFailure"),
        ("StatusNotification", {"status": 3}, None),
        ("StartTransaction", {"connectorId": 1, "idTag": "***4F2A", "meterStart": 1234500}, "1234.5 kWh"),
        ("StartTransaction", {"meterStart": "x"}, None),
        ("StopTransaction", {"meterStop": 1241921, "reason": "EVDisconnected"}, "1241.9 kWh · EVDisconnected"),
        ("StopTransaction", {"meterStop": " 10 "}, "0.0 kWh"),
        ("StopTransaction", {"reason": "Local"}, "Local"),
        ("Authorize", {"idTag": "***4F2A"}, "***4F2A"),
        ("Authorize", {}, None),
        (
            "BootNotification",
            {"chargePointVendor": "KEBA", "chargePointModel": "P30", "firmwareVersion": "3.10"},
            "KEBA P30 · 3.10",
        ),
        ("BootNotification", {"chargePointModel": "P30"}, "P30"),
        ("BootNotification", {}, None),
        ("Heartbeat", {}, None),
        ("Heartbeat", {"currentTime": "2026-10-15T12:00:00Z"}, "2026-10-15T12:00:00Z"),
        ("Reset", {"type": "Soft"}, None),
        (None, {}, None),
    ],
)
def test_headline(action, payload, expected):
    assert headline(action, payload) == expected


@pytest.mark.parametrize("action", sorted(HEADLINES))
@pytest.mark.parametrize("payload", [None, [], "x", {"meterValue": [{"sampledValue": [None, 3]}]}])
def test_headline_never_raises_on_malformed_payloads(action, payload):
    headline(action, payload)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(" 1.2", 1.2), (3, 3.0), (2.5, 2.5), ("", None), ("abc", None), (True, None), (None, None)],
)
def test_number(value, expected):
    assert number(value) == expected


def test_latest_reading_prefers_the_newest_meter_value_and_the_phase_total():
    payload = meter(
        {"value": "230", "measurand": POWER, "phase": "L1"},
        {"value": "690", "measurand": POWER},
        more=[{"sampledValue": [{"value": "1", "measurand": POWER}]}],
    )
    assert latest_reading(payload, POWER) == 690.0


def test_latest_reading_falls_back_to_a_phase_when_no_total_exists():
    assert latest_reading(meter({"value": "230", "measurand": POWER, "phase": "L1"}), POWER) == 230.0
