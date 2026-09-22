"""Forecast evaluation and aggregate score calculations."""

from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def select_eval_snapshot(snapshots: list[dict], market: str, target: date,
                         tz_name: str) -> dict | None:
    cutoff = datetime(target.year, target.month, target.day,
                      tzinfo=ZoneInfo(tz_name)).astimezone(timezone.utc)
    candidates = [snapshot for snapshot in snapshots
                  if snapshot.get("market") == market
                  and snapshot.get("target_date") == target.isoformat()
                  and parse_timestamp(snapshot["snapshot_at"]) < cutoff]
    return max(candidates, key=lambda item: parse_timestamp(item["snapshot_at"]),
               default=None)


def brier_score(probabilities: dict[str, float], actual: str) -> float:
    return sum((probability - (1.0 if label == actual else 0.0)) ** 2
               for label, probability in probabilities.items())


def normalized_brier(probabilities: dict[str, float], actual: str) -> float | None:
    total = sum(probabilities.values())
    if total <= 0:
        return None
    normalized = {label: value / total for label, value in probabilities.items()}
    return brier_score(normalized, actual)


def _score_group(rows: list[dict]) -> dict:
    ordered = sorted((row for row in rows if row.get("brier") is not None),
                     key=lambda row: row["target_date"], reverse=True)[:30]
    n = len(ordered)
    market_values = [row["market_brier"] for row in ordered
                     if row.get("market_brier") is not None]
    return {
        "hits": sum(1 for row in ordered if row.get("hit")),
        "n": n,
        "brier": (sum(row["brier"] for row in ordered) / n) if n else None,
        "market_brier": ((sum(market_values) / len(market_values))
                         if market_values else None),
    }


def build_summary(results: list[dict], paper: list[dict], market_keys: list[str],
                  generated_at: str) -> dict:
    evaluated = [row for row in results if row.get("brier") is not None]
    markets = {key: _score_group([row for row in evaluated if row["market"] == key])
               for key in market_keys}

    resolved = [trade for trade in paper if trade.get("status") in {"won", "lost"}]
    pnl_by_day: dict[str, float] = {}
    for trade in resolved:
        day = (trade.get("settled_at") or trade.get("target_date"))[:10]
        pnl_by_day[day] = pnl_by_day.get(day, 0.0) + float(trade.get("pnl") or 0.0)
    running = 0.0
    series = []
    for day in sorted(pnl_by_day):
        running += pnl_by_day[day]
        series.append({"date": day, "pnl": running})

    bins = [{"lo": index / 10, "hi": (index + 1) / 10,
             "n": 0, "avg_p": None, "hit_rate": None,
             "_sum_p": 0.0, "_hits": 0} for index in range(10)]
    for row in evaluated:
        actual = row["our_bucket"]
        for label, probability in row.get("probs", {}).items():
            index = min(int(float(probability) * 10), 9)
            item = bins[index]
            item["n"] += 1
            item["_sum_p"] += float(probability)
            item["_hits"] += int(label == actual)
    for item in bins:
        if item["n"]:
            item["avg_p"] = item.pop("_sum_p") / item["n"]
            item["hit_rate"] = item.pop("_hits") / item["n"]
        else:
            item.pop("_sum_p")
            item.pop("_hits")

    return {
        "generated_at": generated_at,
        "markets": markets,
        "all": _score_group(evaluated),
        "paper": {
            "trades": len(paper),
            "resolved": len(resolved),
            "wins": sum(1 for trade in resolved if trade["status"] == "won"),
            "win_rate": ((sum(1 for trade in resolved if trade["status"] == "won")
                          / len(resolved)) if resolved else None),
            "pnl": sum(float(trade.get("pnl") or 0.0) for trade in resolved),
            "series": series,
        },
        "calibration": bins,
    }
