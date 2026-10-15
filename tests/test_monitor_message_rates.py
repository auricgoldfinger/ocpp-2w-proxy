from ocpp_2w_proxy.monitor.message_rates import MessageRates

T0 = 1_000_000 * 60.0  # on a minute boundary


def test_counts_frames_per_charger_and_peer():
    rates = MessageRates()
    for at in (T0, T0 + 1, T0 + 61):
        rates.count("cp", "primary", at)
    rates.count("cp", "stats", T0)

    assert rates.last_hour("cp", "primary", T0 + 120) == 3
    assert rates.last_hour("cp", "stats", T0 + 120) == 1
    assert rates.last_hour("cp", "nobody", T0) == 0


def test_frames_older_than_an_hour_no_longer_count():
    rates = MessageRates()
    rates.count("cp", "primary", T0)
    rates.count("cp", "primary", T0 + 1800)

    assert rates.last_hour("cp", "primary", T0 + 3599) == 2
    assert rates.last_hour("cp", "primary", T0 + 3600) == 1
    assert rates.last_hour("cp", "primary", T0 + 9000) == 0


def test_keeps_at_most_an_hour_of_buckets():
    rates = MessageRates()
    for minute in range(500):
        rates.count("cp", "primary", T0 + minute * 60)

    assert len(rates._buckets[("cp", "primary")]) == 60
    assert rates.last_hour("cp", "primary", T0 + 499 * 60) == 60


def test_a_clock_step_back_counts_in_the_newest_bucket():
    rates = MessageRates()
    rates.count("cp", "primary", T0 + 120)
    rates.count("cp", "primary", T0)

    assert rates.last_hour("cp", "primary", T0 + 120) == 2
