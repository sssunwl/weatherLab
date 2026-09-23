"""回測:拿「前一天發出的預報」跟實測對答案,看我們會選哪一格、錯幾格。

用法:
  python3 tools/backtest.py            # 三個市場各 30 天
  python3 tools/backtest.py hong-kong 45

偏差校正用「測試期之前」的資料算,不是同一批(避免自己考自己)。
預報用 Open-Meteo Historical Forecast API 的 temperature_2m_previous_day1,
也就是目標日前一天發出的那一版,對應我們每天 08:00 快照的時點。
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

from lab.buckets import bucket_for_value, settle_value
from lab.http import HttpClient
from lab.probability import bucket_probabilities
from lab.sources import HISTORICAL_URL, fetch_actuals, fetch_gamma_market
from lab.storage import ROOT
from lab.timeutil import local_midnight_utc

PRICES_URL = "https://clob.polymarket.com/prices-history"

MODELS = ("ecmwf_ifs025", "gfs_seamless")


def day_ahead_forecasts(client, config, start: date, end: date) -> dict[str, float]:
    """回傳 {日期: 前一天預報的日最高溫},兩個模型取平均。"""
    unit = "fahrenheit" if config["unit"] == "F" else "celsius"
    per_model: dict[str, list[float]] = {}
    for model in MODELS:
        payload = client.json(HISTORICAL_URL, params={
            "latitude": config["lat"], "longitude": config["lon"],
            "start_date": start.isoformat(), "end_date": end.isoformat(),
            "hourly": "temperature_2m_previous_day1",
            "models": model, "timezone": config["tz"], "temperature_unit": unit,
        })
        hourly = payload["hourly"]
        series = next(values for key, values in hourly.items()
                      if key.startswith("temperature_2m_previous_day1"))
        daily: dict[str, float] = {}
        for stamp, value in zip(hourly["time"], series):
            if value is None:
                continue
            day = stamp[:10]
            daily[day] = max(float(value), daily.get(day, float(value)))
        for day, value in daily.items():
            per_model.setdefault(day, []).append(value)
    return {day: sum(values) / len(values) for day, values in per_model.items()
            if len(values) == len(MODELS)}


def market_prices_at(client, gamma: list[dict], cutoff_ts: int) -> list[float | None]:
    """每個格子在 cutoff(目標日當地 00:00)之前的最後成交價。"""
    prices = []
    for item in gamma:
        history = client.json(PRICES_URL, params={
            "market": item["token_id"], "interval": "max", "fidelity": 60,
        }).get("history", [])
        earlier = [point["p"] for point in history if point["t"] <= cutoff_ts]
        prices.append(float(earlier[-1]) if earlier else None)
    return prices


def brier(probabilities: list[float], labels: list[str], actual: str) -> float:
    return sum((p - (1.0 if label == actual else 0.0)) ** 2
               for p, label in zip(probabilities, labels))


def run(config: dict, days: int, window: int = 30, with_market: bool = False) -> dict:
    client = HttpClient()
    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=days - 1)
    history_start = start - timedelta(days=window)

    forecasts = day_ahead_forecasts(client, config, history_start, end)
    actuals = fetch_actuals(client, config, history_start, end)

    def rolling_bias(target: date) -> tuple[float, int]:
        """只用目標日前 `window` 天的誤差,每天重算(季節轉換時才跟得上)。"""
        residuals = []
        for back in range(1, window + 1):
            day = (target - timedelta(days=back)).isoformat()
            if day in forecasts and day in actuals:
                residuals.append(forecasts[day] - actuals[day])
        if not residuals:
            return 0.0, 0.0
        mean = sum(residuals) / len(residuals)
        spread = (sum((value - mean) ** 2 for value in residuals)
                  / len(residuals)) ** 0.5 if len(residuals) > 1 else 1.0
        return mean, max(spread, 0.4)

    rows = []
    for offset in range(days):
        target = start + timedelta(days=offset)
        day = target.isoformat()
        if day not in forecasts or day not in actuals:
            continue
        bias, sigma = rolling_bias(target)
        predicted = forecasts[day] - bias
        observed = settle_value(actuals[day], config["settle_rule"])
        try:
            gamma = fetch_gamma_market(client, config, target)
        except Exception:
            gamma = None
        if not gamma:
            continue
        buckets = [{key: item[key] for key in ("label", "lo", "hi")} for item in gamma]
        winner = next((item["label"] for item in gamma if item["mid"] > 0.99), None)
        our_bucket = bucket_for_value(
            settle_value(predicted, config["settle_rule"]), buckets)
        actual_bucket = bucket_for_value(observed, buckets)
        labels = [item["label"] for item in buckets]
        gap = (abs(labels.index(our_bucket) - labels.index(actual_bucket))
               if our_bucket in labels and actual_bucket in labels else None)
        our_probabilities = bucket_probabilities([predicted], buckets,
                                                 config["settle_rule"], sigma)
        market_probabilities = None
        if with_market:
            cutoff = int(local_midnight_utc(target, config["tz"]).timestamp())
            raw = market_prices_at(client, gamma, cutoff)
            total = sum(value for value in raw if value is not None)
            if total > 0:
                market_probabilities = [(value / total) if value is not None else 0.0
                                        for value in raw]
        rows.append({
            "our_brier": brier(our_probabilities, labels, actual_bucket),
            "market_brier": (brier(market_probabilities, labels, actual_bucket)
                             if market_probabilities else None),
            "market_top": (labels[market_probabilities.index(max(market_probabilities))]
                           if market_probabilities else None),
            "p_actual": our_probabilities[labels.index(actual_bucket)]
            if actual_bucket in labels else None,
            "date": day, "forecast": predicted, "actual_raw": actuals[day],
            "actual_value": observed, "our_bucket": our_bucket,
            "actual_bucket": actual_bucket, "market_bucket": winner,
            "hit": our_bucket == actual_bucket, "gap": gap, "bias": bias,
            "error": predicted - actuals[day],
        })
    return {"market": config["key"], "name": config["name"], "unit": config["unit"],
            "window": window, "rows": rows}


def main(argv: list[str]) -> int:
    configs = json.loads((ROOT / "config" / "markets.json").read_text())
    wanted = argv[0] if argv else None
    days = int(argv[1]) if len(argv) > 1 else 30
    window = int(argv[2]) if len(argv) > 2 else 30
    for config in configs:
        if wanted and config["key"] != wanted:
            continue
        if not wanted and not config["enabled"]:
            continue
        report = run(config, days, window, with_market=True)
        rows = report["rows"]
        unit = report["unit"]
        print(f"\n=== {report['name']} ({len(rows)} 天,偏差用前 {report['window']} 天滾動計算) ===")
        print(f"{'日期':<12}{'預報':>7}{'實測':>7}  {'我們選':<14}{'實際':<14}{'結果'}")
        for row in rows:
            mark = "✅" if row["hit"] else f"❌ 差{row['gap']}格"
            flag = "" if row["market_bucket"] in (None, row["actual_bucket"]) else "  ⚠️規則不符"
            print(f"{row['date']:<12}{row['forecast']:>7.1f}{row['actual_raw']:>7.1f}  "
                  f"{row['our_bucket']:<14}{row['actual_bucket']:<14}{mark}{flag}")
        if rows:
            hits = sum(1 for row in rows if row["hit"])
            within1 = sum(1 for row in rows
                          if row["gap"] is not None and row["gap"] <= 1)
            errors = [row["error"] for row in rows]
            mae = sum(abs(value) for value in errors) / len(errors)
            scored = [row for row in rows if row["market_brier"] is not None]
            if scored:
                market_hits = sum(1 for row in scored
                                  if row["market_top"] == row["actual_bucket"])
                print(f"  vs 市場({len(scored)} 天):我們命中 "
                      f"{sum(1 for row in scored if row['hit'])}/{len(scored)}、"
                      f"市場命中 {market_hits}/{len(scored)};"
                      f"Brier 我們 {sum(row['our_brier'] for row in scored) / len(scored):.3f}、"
                      f"市場 {sum(row['market_brier'] for row in scored) / len(scored):.3f}"
                      "(越低越好)")
            print(f"命中 {hits}/{len(rows)} ({hits / len(rows):.0%}) · "
                  f"差 1 格內 {within1}/{len(rows)} ({within1 / len(rows):.0%}) · "
                  f"平均絕對誤差 {mae:.2f}°{unit} · "
                  f"最大誤差 {max(errors, key=abs):+.1f}°{unit}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
