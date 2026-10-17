from ocpp_2w_proxy.durations import format_duration


def test_short_durations_keep_a_decimal_and_longer_ones_use_whole_units():
    assert [format_duration(s) for s in (0.04, 1.84, 42, 421, 7380)] == ["0.0s", "1.8s", "42s", "7m01s", "2h03m"]
