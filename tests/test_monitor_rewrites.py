import pytest

from ocpp_2w_proxy.monitor.rewrites import rewritten

METER = {"connectorId": 1, "transactionId": 10216, "meterValue": []}
REMOTE_START = {"idTag": "***4F2A", "chargingProfile": {"chargingProfileId": 4, "stackLevel": 0}}


@pytest.mark.parametrize(
    ("origin", "leg", "expected"),
    [
        (METER, METER, []),
        (METER, dict(METER), []),
        (METER, {**METER, "transactionId": 1791822333}, [{"field": "tx", "from": 10216, "to": 1791822333}]),
        (METER, {"connectorId": 1, "meterValue": []}, [{"field": "tx", "from": 10216, "to": None}]),
        (
            {"csChargingProfiles": {"transactionId": 77, "stackLevel": 1}},
            {"csChargingProfiles": {"transactionId": 10216, "stackLevel": 1}},
            [{"field": "tx", "from": 77, "to": 10216}],
        ),
        (REMOTE_START, {"idTag": "***4F2A"}, [{"field": "profile", "from": 4, "to": None}]),
        ({"chargingProfile": {}}, {}, [{"field": "profile", "from": "present", "to": None}]),
        ({"idTag": "A"}, {"idTag": "B"}, [{"field": "id", "from": "A", "to": "B"}]),
        # a difference in an untracked field changes the payload but earns no chip
        (METER, {**METER, "connectorId": 2}, []),
    ],
)
def test_rewritten(origin, leg, expected):
    assert rewritten(origin, leg) == expected
