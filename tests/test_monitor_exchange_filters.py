import pytest

from ocpp_2w_proxy.minihttp import HttpError
from ocpp_2w_proxy.monitor.exchange_filters import parse_exchange_query
from ocpp_2w_proxy.monitor.queries import ExchangeQuery


def test_no_parameters_ask_for_everything():
    assert parse_exchange_query({}) == ExchangeQuery()


def test_every_parameter_is_parsed():
    query = parse_exchange_query(
        {
            "charger": "99224881",
            "action": "MeterValues",
            "outcome": "queued",
            "peer": "stats",
            "problem": "1",
            "tx": "10216",
            "q": "c0ffee",
            "exclude": "Heartbeat, StatusNotification,",
            "since": "1760536931.21",
            "before": "1760536931.3:42",
            "limit": "50",
        }
    )

    assert query == ExchangeQuery(
        charger="99224881",
        action="MeterValues",
        outcome="queued",
        peer="stats",
        problem=True,
        tx=10216,
        q="c0ffee",
        exclude=("Heartbeat", "StatusNotification"),
        since=1760536931.21,
        before="1760536931.3:42",
        limit=50,
    )


def test_blank_values_are_not_filters():
    assert parse_exchange_query({"charger": "", "tx": "", "problem": "", "limit": ""}) == ExchangeQuery()


@pytest.mark.parametrize(
    "parameters",
    [
        {"outcome": "lost"},
        {"problem": "yes"},
        {"tx": "ten"},
        {"tx": "1_000"},
        {"tx": " 12"},
        {"tx": str(2**63)},
        {"since": "yesterday"},
        {"since": "nan"},
        {"since": "inf"},
        {"limit": "0"},
        {"limit": "-5"},
        {"limit": "2.5"},
        {"page": "2"},
    ],
)
def test_a_malformed_value_or_unknown_parameter_is_a_400(parameters):
    with pytest.raises(HttpError) as refused:
        parse_exchange_query(parameters)
    assert (refused.value.status, refused.value.code) == (400, "bad_request")
