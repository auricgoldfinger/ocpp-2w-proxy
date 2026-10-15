from ocpp_2w_proxy.ocpp import Call
from ocpp_2w_proxy.redact import describe, describe_credentials, redact_payload


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
