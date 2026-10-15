import pytest

from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.redact import describe, describe_credentials, mask_url_credentials, redact_payload


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
