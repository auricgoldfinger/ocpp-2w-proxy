import pytest

from ocpp_2w_proxy.monitor.gaps import ExchangeSpan, Gap, GapDetector, incomplete_exchanges


def test_no_drops_no_gap():
    detector = GapDetector(started_at=0.0)

    assert detector.observe({}, {}, now=1.0) == []
    assert detector.observe({"cp": 0}, {}, now=2.0) == []


def test_counter_growth_becomes_a_gap_from_the_previous_observation_to_the_last_drop():
    detector = GapDetector(started_at=0.0)
    detector.observe({"cp": 0}, {}, now=10.0)

    gaps = detector.observe({"cp": 37, "other": 0}, {"cp": 12.5}, now=13.0)

    assert gaps == [Gap("cp", 10.0, 12.5, 37)]


def test_only_drops_since_the_previous_observation_count():
    detector = GapDetector(started_at=0.0)
    detector.observe({"cp": 5}, {"cp": 1.0}, now=2.0)

    assert detector.observe({"cp": 5}, {"cp": 1.0}, now=3.0) == []
    assert detector.observe({"cp": 8}, {"cp": 3.5}, now=4.0) == [Gap("cp", 3.0, 3.5, 3)]


def test_missing_drop_time_falls_back_to_now():
    detector = GapDetector(started_at=0.0)

    assert detector.observe({"cp": 1}, {}, now=4.0) == [Gap("cp", 0.0, 4.0, 1)]


GAP = Gap("cp", 10.0, 12.0, 5)


def span(started_at, last_at, *, open=False, orphan=False, charger="cp", exchange_id=1):
    return ExchangeSpan(exchange_id, charger, started_at, last_at, open, orphan)


@pytest.mark.parametrize(
    ("exchange", "incomplete"),
    [
        (span(9.0, 11.0), True),  # active across the gap's start
        (span(10.5, 11.0), True),  # entirely inside
        (span(11.0, 20.0), True),  # started inside, ended after
        (span(5.0, 9.0), False),  # ended before
        (span(1.0, 2.0, open=True), True),  # still open
        (span(13.0, 14.0, open=True), False),  # started after
        (span(10.0, 11.0, charger="other"), False),
        (span(13.0, 13.0, orphan=True), True),  # reply to a Call lost in the gap
        (span(50.0, 50.0, orphan=True), False),
        (span(9.0, 9.0, orphan=True), False),
    ],
)
def test_incomplete_exchanges(exchange, incomplete):
    assert incomplete_exchanges(GAP, [exchange], reply_window=30.0) == ([1] if incomplete else [])
