import base64

import pytest

from ocpp_2w_proxy.charger_auth import AuthRejected, authenticate, charger_id_from_path
from ocpp_2w_proxy.config import ChargerConfig

OPEN = {"CH1": ChargerConfig("CH1", None, "CH1", "CH1")}
LOCKED = {"CH1": ChargerConfig("CH1", "s3cret", "CH1", "CH1")}


def basic(user: str, password: str) -> str:
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


@pytest.mark.parametrize(
    ("path", "expected"), [("/CH1", "CH1"), ("/ocpp/CH1", "CH1"), ("/ocpp/CH-1_a.b/?x=1", "CH-1_a.b")]
)
def test_charger_id_is_last_path_segment(path, expected):
    assert charger_id_from_path(path) == expected


@pytest.mark.parametrize("path", ["/", "/ocpp/..%2Fetc", "/a%20b", "/" + "x" * 65])
def test_invalid_charger_ids_rejected(path):
    with pytest.raises(AuthRejected):
        charger_id_from_path(path)


def test_unknown_charger_rejected():
    with pytest.raises(AuthRejected) as exc:
        authenticate("/OTHER", None, None, OPEN)
    assert exc.value.status == 404


def test_no_password_configured_accepts_missing_or_any_password():
    assert authenticate("/CH1", None, None, OPEN).charger.id == "CH1"
    assert authenticate("/CH1", basic("CH1", "whatever"), None, OPEN).authorization_header


def test_username_must_match_charger_id():
    with pytest.raises(AuthRejected):
        authenticate("/CH1", basic("CH2", ""), None, OPEN)


def test_configured_password_is_enforced():
    assert authenticate("/CH1", basic("CH1", "s3cret"), None, LOCKED)
    for header in (None, basic("CH1", "wrong"), basic("CH1", ""), "Bearer abc", "Basic !!!"):
        with pytest.raises(AuthRejected):
            authenticate("/CH1", header, None, LOCKED)
