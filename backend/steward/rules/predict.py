"""Predictive pressure: warn *before* a threshold is crossed, not after.

Deterministic and dependency-free. We smooth a metric series with an EWMA to
kill noise, fit a simple least-squares trend to the smoothed tail, and project
when (if ever) it will cross a threshold. The LLM is not involved; if we later
want narration, the decision still lives here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


def ewma(values: list[float], alpha: float = 0.3) -> list[float]:
    """Exponentially weighted moving average. ``alpha`` in (0, 1]."""
    if not values:
        return []
    out = [values[0]]
    for v in values[1:]:
        out.append(alpha * v + (1 - alpha) * out[-1])
    return out


def linear_slope(values: list[float]) -> float:
    """Least-squares slope of ``values`` vs their index (units: per sample)."""
    n = len(values)
    if n < 2:
        return 0.0
    xs = range(n)
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs)
    return num / den if den else 0.0


@dataclass
class Forecast:
    current: float          # latest smoothed value
    slope_per_s: float      # trend (metric units per second)
    seconds_to_threshold: Optional[float]  # None if not trending toward it
    will_cross: bool


def forecast_threshold(
    values: list[float],
    *,
    threshold: float,
    dt_s: float,
    alpha: float = 0.3,
    lookahead_s: float = 1800.0,
    tail: int = 12,
) -> Optional[Forecast]:
    """Project whether the smoothed series crosses ``threshold`` within
    ``lookahead_s``. Returns ``None`` if there isn't enough data."""
    if len(values) < 3 or dt_s <= 0:
        return None
    smoothed = ewma(values, alpha)
    window = smoothed[-tail:]
    slope_per_sample = linear_slope(window)
    slope_per_s = slope_per_sample / dt_s
    current = smoothed[-1]

    if current >= threshold:
        return Forecast(current, slope_per_s, 0.0, True)
    if slope_per_s <= 0:
        return Forecast(current, slope_per_s, None, False)

    seconds = (threshold - current) / slope_per_s
    will_cross = seconds <= lookahead_s
    return Forecast(current, slope_per_s, seconds if will_cross else None, will_cross)
