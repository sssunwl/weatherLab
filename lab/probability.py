"""Ensemble-to-bucket probability calculations."""

from __future__ import annotations

import math
import statistics


def _cdf(value: float, mean: float, sigma: float) -> float:
    if value == math.inf:
        return 1.0
    if value == -math.inf:
        return 0.0
    return 0.5 * (1.0 + math.erf((value - mean) / (sigma * math.sqrt(2.0))))


def _bounds(bucket: dict, rule: str) -> tuple[float, float]:
    lo, hi = bucket["lo"], bucket["hi"]
    if rule == "round":
        return (
            -math.inf if lo is None else lo - 0.5,
            math.inf if hi is None else hi + 0.5,
        )
    if rule == "floor":
        return (
            -math.inf if lo is None else float(lo),
            math.inf if hi is None else hi + 1.0,
        )
    raise ValueError(f"unknown settlement rule: {rule}")


def bucket_probabilities(members: list[float], buckets: list[dict], rule: str,
                         sigma: float) -> list[float]:
    if not members:
        raise ValueError("no usable ensemble members")
    if sigma <= 0:
        raise ValueError("kernel sigma must be positive")
    probabilities = []
    for bucket in buckets:
        low, high = _bounds(bucket, rule)
        p = sum(_cdf(high, member, sigma) - _cdf(low, member, sigma)
                for member in members) / len(members)
        probabilities.append(p)
    total = sum(probabilities)
    if abs(total - 1.0) >= 1e-6:
        raise ValueError(f"bucket probabilities sum to {total:.9f}, not 1")
    return probabilities


def percentile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("cannot take percentile of empty list")
    ordered = sorted(values)
    position = (len(ordered) - 1) * q
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] * (upper - position) + ordered[upper] * (position - lower)


def member_stats(values: list[float]) -> dict:
    return {
        "n_members": len(values),
        "mean": statistics.fmean(values),
        "p10": percentile(values, 0.10),
        "p50": percentile(values, 0.50),
        "p90": percentile(values, 0.90),
    }


def daily_member_maxima(hourly: dict, target_date: str) -> list[float]:
    times = hourly.get("time", [])
    indices = [index for index, value in enumerate(times)
               if isinstance(value, str) and value[:10] == target_date]
    if not indices:
        raise ValueError(f"ensemble contains no hours for {target_date}")
    maxima = []
    for key, series in hourly.items():
        if not key.startswith("temperature_2m") or key == "time":
            continue
        values = [series[index] for index in indices
                  if index < len(series) and series[index] is not None]
        if values:
            maxima.append(max(float(value) for value in values))
    if not maxima:
        raise ValueError(f"ensemble contains no usable members for {target_date}")
    return maxima
