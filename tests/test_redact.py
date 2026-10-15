import json

import pytest

from ocpp_2w_proxy.ocpp import OCPP_ACTIONS, Call
from ocpp_2w_proxy.redact import (
    REDACT_ACTIONS,
    describe,
    describe_credentials,
    mask_url_credentials,
    redact_message_payload,
    redact_payload,
)


def test_redaction_masks_id_tags_everywhere():
    payload = {
        "idTag": "04A2B3C4D5",
        "idTagInfo": {"status": "Accepted", "parentIdTag": "PARENT1234"},
        "localAuthorizationList": [{"idTag": "AB"}],
    }
    redacted = redact_payload(payload)
    assert redacted["idTag"] == "***C4D5"
    assert redacted["idTagInfo"]["parentIdTag"] == "***1234"
    assert redacted["localAuthorizationList"][0]["idTag"] == "***"
    assert "04A2B3C4D5" not in describe(Call("1", "Authorize", payload), include_payload=True)
    assert "04A2" not in describe(Call("1", "Authorize", payload), include_payload=False)


def test_credentials_never_printed():
    text = describe_credentials("CH1", "supersecret")
    assert "supersecret" not in text and "present" in text


def test_authorization_key_value_is_hidden_case_insensitively():
    change = redact_payload({"key": "AuthorizationKey", "value": "0011223344556677"})
    listed = redact_payload({"configurationKey": [{"key": "authorizationkey", "readonly": False, "value": "secret"}]})
    assert change == {"key": "AuthorizationKey", "value": "***"}
    assert listed["configurationKey"][0] == {"key": "authorizationkey", "readonly": False, "value": "***"}


def test_other_configuration_values_stay_visible():
    payload = {"key": "HeartbeatInterval", "value": "300"}
    assert redact_payload(payload) == payload


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("ftp://user:pa55@files.example.com/diag", "ftp://***@files.example.com/diag"),
        ("https://user@example.com:8443/fw.bin", "https://***@example.com:8443/fw.bin"),
        ("ftp://u:p@ss@host/x", "ftp://***@host/x"),
        ("ftp://user:pa/ss@host/x", "***"),
        ("user:pass@host/x", "***"),
        ("http://[::1/x", "***"),
        ("https://example.com/fw.bin", "https://example.com/fw.bin"),
    ],
)
def test_url_credentials_are_hidden(url, expected):
    assert mask_url_credentials(url) == expected


def test_location_urls_sim_ids_and_vendor_data_are_masked():
    redacted = redact_payload(
        {
            "location": "ftp://user:secret@host/fw",
            "iccid": "8931440400000012345",
            "imsi": "204080000012345",
            "data": '{"pin": "1234"}',
            "vendorId": "com.example",
        }
    )
    assert redacted == {
        "location": "ftp://***@host/fw",
        "iccid": "***2345",
        "imsi": "***2345",
        "data": "***",
        "vendorId": "com.example",
    }


def test_meter_value_location_is_kept():
    sample = {"value": "1.2", "measurand": "Energy.Active.Import.Register", "location": "Outlet"}
    redacted = redact_payload({"meterValue": [{"sampledValue": [sample]}]})
    assert redacted["meterValue"][0]["sampledValue"] == [sample]


# Plan §6: one example per secret-carrying field, as a Call or CallResult payload, with the secret it holds.
_SECRET_PAYLOADS = [
    ("Authorize", {"idTag": "CARD12345678"}, "CARD1234"),
    ("Authorize", {"idTagInfo": {"status": "Accepted", "parentIdTag": "PARENT987654"}}, "PARENT98"),
    ("StartTransaction", {"connectorId": 1, "idTag": "CARD12345678", "meterStart": 0}, "CARD1234"),
    ("StartTransaction", {"transactionId": 7, "idTagInfo": {"parentIdTag": "PARENT987654"}}, "PARENT98"),
    ("StopTransaction", {"transactionId": 7, "idTag": "CARD12345678", "meterStop": 9}, "CARD1234"),
    ("StopTransaction", {"idTagInfo": {"status": "Accepted", "parentIdTag": "PARENT987654"}}, "PARENT98"),
    ("RemoteStartTransaction", {"idTag": "CARD12345678"}, "CARD1234"),
    ("ReserveNow", {"connectorId": 1, "idTag": "CARD12345678", "reservationId": 3}, "CARD1234"),
    ("ReserveNow", {"idTag": "x", "parentIdTag": "PARENT987654"}, "PARENT98"),
    ("SendLocalList", {"listVersion": 2, "localAuthorizationList": [{"idTag": "CARD12345678"}]}, "CARD1234"),
    (
        "SendLocalList",
        {"localAuthorizationList": [{"idTag": "x", "idTagInfo": {"parentIdTag": "PARENT987654"}}]},
        "PARENT98",
    ),
    ("ChangeConfiguration", {"key": "AuthorizationKey", "value": "S3CR3T-KEY"}, "S3CR3T"),
    ("GetConfiguration", {"configurationKey": [{"key": "AuthorizationKey", "value": "S3CR3T-KEY"}]}, "S3CR3T"),
    ("GetDiagnostics", {"location": "ftp://diag:S3CR3T@host/up"}, "S3CR3T"),
    ("UpdateFirmware", {"location": "https://fw:S3CR3T@host/fw.bin", "retrieveDate": "x"}, "S3CR3T"),
    ("DataTransfer", {"vendorId": "v", "data": "S3CR3T-BLOB"}, "S3CR3T"),
    ("DataTransfer", {"status": "Accepted", "data": "S3CR3T-BLOB"}, "S3CR3T"),
    ("BootNotification", {"chargePointVendor": "v", "iccid": "8931440400000012345"}, "89314404"),
    ("BootNotification", {"chargePointVendor": "v", "imsi": "204080000012345"}, "20408000"),
]


@pytest.mark.parametrize(("action", "payload", "secret"), _SECRET_PAYLOADS)
def test_monitor_and_logs_never_show_a_listed_secret(action, payload, secret):
    assert action in REDACT_ACTIONS
    assert secret not in json.dumps(redact_message_payload(action, payload))
    assert secret not in describe(Call("1", action, payload), include_payload=True)


# Every OCPP 1.6 action with the secret-carrying fields of its Call and CallResult (from the 1.6 schemas):
# card tags, configuration keys, URLs, SIM ids and opaque vendor data. Keyed by every action, so a new
# action forces a decision here.
_SECRET_FIELDS_BY_ACTION = {
    "Authorize": {"idTag", "parentIdTag"},
    "BootNotification": {"iccid", "imsi"},
    "DataTransfer": {"data"},
    "DiagnosticsStatusNotification": set(),
    "FirmwareStatusNotification": set(),
    "Heartbeat": set(),
    "MeterValues": set(),
    "StartTransaction": {"idTag", "parentIdTag"},
    "StatusNotification": set(),
    "StopTransaction": {"idTag", "parentIdTag"},
    "CancelReservation": set(),
    "ChangeAvailability": set(),
    "ChangeConfiguration": {"key", "value"},
    "ClearCache": set(),
    "ClearChargingProfile": set(),
    "GetCompositeSchedule": set(),
    "GetConfiguration": {"configurationKey"},
    "GetDiagnostics": {"location"},
    "GetLocalListVersion": set(),
    "RemoteStartTransaction": {"idTag"},
    "RemoteStopTransaction": set(),
    "ReserveNow": {"idTag", "parentIdTag"},
    "Reset": set(),
    "SendLocalList": {"idTag", "parentIdTag"},
    "SetChargingProfile": set(),
    "TriggerMessage": set(),
    "UnlockConnector": set(),
    "UpdateFirmware": {"location"},
}


def test_every_action_with_a_secret_field_is_redacted():
    assert _SECRET_FIELDS_BY_ACTION.keys() == OCPP_ACTIONS
    secret_actions = {action for action, fields in _SECRET_FIELDS_BY_ACTION.items() if fields}
    assert secret_actions == REDACT_ACTIONS


def test_unknown_action_fails_closed():
    assert redact_message_payload(None, {"idTag": "CARD12345678"}) == {"idTag": "***5678"}


def test_payload_without_secrets_is_returned_unchanged():
    payload = {"connectorId": 1, "meterValue": [{"timestamp": "t", "sampledValue": [{"value": "1.2"}]}]}
    assert redact_message_payload("MeterValues", payload) is payload
