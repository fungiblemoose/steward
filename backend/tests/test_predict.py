from __future__ import annotations

from steward.rules.predict import ewma, forecast_threshold, linear_slope


def test_ewma_smooths():
    out = ewma([0, 10, 0, 10, 0, 10], alpha=0.3)
    assert len(out) == 6
    # smoothed values stay within the data range and move gradually
    assert all(0 <= v <= 10 for v in out)
    assert abs(out[-1] - out[-2]) < 10


def test_linear_slope_positive():
    assert linear_slope([1, 2, 3, 4, 5]) == 1.0
    assert linear_slope([5, 4, 3, 2, 1]) == -1.0
    assert linear_slope([3, 3, 3]) == 0.0


def test_forecast_crossing_predicted():
    # steadily rising from 50 toward 90; dt=10s; should cross within lookahead
    values = [50 + i * 2 for i in range(15)]  # 50..78
    fc = forecast_threshold(values, threshold=90, dt_s=10, lookahead_s=3600)
    assert fc is not None
    assert fc.will_cross is True
    assert fc.seconds_to_threshold and fc.seconds_to_threshold > 0


def test_forecast_no_crossing_when_flat():
    values = [40.0] * 15
    fc = forecast_threshold(values, threshold=90, dt_s=10)
    assert fc is not None and fc.will_cross is False
    assert fc.seconds_to_threshold is None


def test_forecast_no_crossing_when_falling():
    values = [80 - i for i in range(15)]
    fc = forecast_threshold(values, threshold=90, dt_s=10)
    assert fc.will_cross is False


def test_forecast_already_over():
    values = [95.0] * 5
    fc = forecast_threshold(values, threshold=90, dt_s=10)
    assert fc.will_cross is True and fc.seconds_to_threshold == 0.0


def test_forecast_needs_data():
    assert forecast_threshold([1, 2], threshold=90, dt_s=10) is None
