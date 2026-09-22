"""Historical forecast bias estimation."""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta

from .sources import fetch_actuals, fetch_historical_forecasts
def calculate_bias(client, config: dict, now: datetime) -> dict:
    end = now.date() - timedelta(days=1)
    start = end - timedelta(days=59)
    forecasts = fetch_historical_forecasts(client, config, start, end)
    actuals = fetch_actuals(client, config, start, end)
    residuals = [forecast - actuals[day] for day, forecast in forecasts.items()
                 if day in actuals]
    n = len(residuals)
    raw_bias = statistics.fmean(residuals) if residuals else 0.0
    return {
        "bias": raw_bias if n >= 20 else 0.0,
        "resid_sd": statistics.pstdev(residuals) if len(residuals) > 1 else 0.0,
        "n": n,
        "window": [start.isoformat(), end.isoformat()],
        "updated": now.isoformat().replace("+00:00", "Z"),
    }
