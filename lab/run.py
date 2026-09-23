"""weatherLab v2 command-line runner."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path

from .bias import calculate_bias
from .buckets import bucket_for_value, settle_value
from .http import HttpClient
from .notify import build_discord_message
from .probability import bucket_probabilities, daily_member_maxima, member_stats
from .scoring import (brier_score, build_summary, normalized_brier,
                      select_eval_snapshot)
from .sources import add_order_books, fetch_actuals, fetch_ensemble, fetch_gamma_market
from .storage import DATA_DIR, ROOT, append_jsonl, iter_snapshots, read_json, write_json
from .timeutil import local_date, parse_now, ready_to_settle
from .trading import place_trades, settle_trades


def _iso(now: datetime) -> str:
    return now.isoformat().replace("+00:00", "Z")


def _error(errors: list[dict], market: str, stage: str, exc: Exception) -> None:
    errors.append({"market": market, "stage": stage,
                   "error": f"{type(exc).__name__}: {exc}"})


def _gamma_winner(markets: list[dict] | None) -> str | None:
    if not markets:
        return None
    winners = [market["label"] for market in markets if market["mid"] > 0.99]
    return winners[0] if len(winners) == 1 else None


def _last_snapshot(snapshots: list[dict], market: str, target: date) -> dict | None:
    candidates = [snapshot for snapshot in snapshots
                  if snapshot.get("market") == market
                  and snapshot.get("target_date") == target.isoformat()]
    return max(candidates, key=lambda item: item["snapshot_at"], default=None)


def settle_recent(client: HttpClient, configs: list[dict], now: datetime,
                  results: list[dict], snapshots: list[dict], errors: list[dict]
                  ) -> tuple[list[dict], list[dict], list[str]]:
    newly_settled: list[dict] = []
    pending: list[dict] = []
    warnings: list[str] = []
    result_index = {(row["market"], row["target_date"]): row for row in results}
    attempted_market_results: set[tuple[str, str]] = set()

    for config in configs:
        today = local_date(now, config["tz"])
        targets = [today - timedelta(days=offset) for offset in range(1, 8)]
        eligible = [target for target in targets
                    if ready_to_settle(target, config["tz"], now)]
        if not eligible:
            continue
        try:
            actuals = fetch_actuals(client, config, min(eligible), max(eligible))
        except Exception as exc:
            _error(errors, config["key"], "settlement actuals", exc)
            pending.append({"market": config["key"], "target_date": eligible[0].isoformat(),
                            "reason": "實測資料抓取失敗"})
            continue

        for target in eligible:
            key = (config["key"], target.isoformat())
            existing = result_index.get(key)
            if existing is not None and existing.get("market_bucket") is not None:
                continue
            eval_snapshot = select_eval_snapshot(snapshots, config["key"], target,
                                                 config["tz"])
            bucket_snapshot = eval_snapshot or _last_snapshot(snapshots, config["key"], target)
            gamma = None
            attempted_market_results.add(key)
            try:
                gamma = fetch_gamma_market(client, config, target)
            except Exception as exc:
                _error(errors, config["key"], f"settlement market {target}", exc)
            market_winner = _gamma_winner(gamma)
            if existing is not None:
                if market_winner is not None:
                    existing["market_bucket"] = market_winner
                    if existing["our_bucket"] != market_winner:
                        warnings.append(f"{config['name']} {target} 結算不一致")
                continue

            if bucket_snapshot is None:
                continue
            raw = actuals.get(target.isoformat())
            if raw is None:
                reason = "天文台未發佈" if config["key"] == "hong-kong" else "實測尚未發佈"
                pending.append({"market": config["key"], "target_date": target.isoformat(),
                                "reason": reason})
                continue
            bucket_defs = ([{key: bucket[key] for key in ("label", "lo", "hi")}
                            for bucket in bucket_snapshot["buckets"]]
                           if bucket_snapshot else
                           [{key: bucket[key] for key in ("label", "lo", "hi")}
                            for bucket in (gamma or [])])
            if not bucket_defs:
                _error(errors, config["key"], f"settlement buckets {target}",
                       ValueError("no bucket definitions available"))
                continue
            value = settle_value(raw, config["settle_rule"])
            actual_bucket = bucket_for_value(value, bucket_defs)
            if actual_bucket is None:
                _error(errors, config["key"], f"settlement bucket {target}",
                       ValueError(f"no bucket contains {value}"))
                continue

            probabilities = ({bucket["label"]: bucket["p"]
                              for bucket in eval_snapshot["buckets"]}
                             if eval_snapshot else {})
            market_mids = ({bucket["label"]: bucket["mid"]
                            for bucket in eval_snapshot["buckets"]}
                           if eval_snapshot else {})
            top_bucket = (max(probabilities, key=probabilities.get)
                          if probabilities else None)
            row = {
                "market": config["key"],
                "target_date": target.isoformat(),
                "obs_raw": raw,
                "obs_value": value,
                "our_bucket": actual_bucket,
                "market_bucket": market_winner,
                "eval_snapshot_at": eval_snapshot["snapshot_at"] if eval_snapshot else None,
                "probs": probabilities,
                "market_mids": market_mids,
                "p_of_actual": probabilities.get(actual_bucket),
                "top_bucket": top_bucket,
                "top_p": probabilities.get(top_bucket) if top_bucket else None,
                "hit": (top_bucket == actual_bucket) if top_bucket else None,
                "brier": brier_score(probabilities, actual_bucket) if probabilities else None,
                "market_brier": normalized_brier(market_mids, actual_bucket)
                if market_mids else None,
                "settled_at": _iso(now),
            }
            results.append(row)
            result_index[key] = row
            newly_settled.append(row)
            if market_winner is not None and actual_bucket != market_winner:
                warnings.append(f"{config['name']} {target} 結算不一致")

    configs_by_key = {config["key"]: config for config in configs}
    for row in results:
        key = (row["market"], row["target_date"])
        if row.get("market_bucket") is not None or key in attempted_market_results:
            continue
        config = configs_by_key.get(row["market"])
        if config is None:
            continue
        try:
            gamma = fetch_gamma_market(client, config, date.fromisoformat(row["target_date"]))
            winner = _gamma_winner(gamma)
            if winner is not None:
                row["market_bucket"] = winner
                if row["our_bucket"] != winner:
                    warnings.append(f"{config['name']} {row['target_date']} 結算不一致")
        except Exception as exc:
            _error(errors, config["key"], f"market result {row['target_date']}", exc)
    results.sort(key=lambda row: (row["target_date"], row["market"]))
    return newly_settled, pending, warnings


def create_snapshots(client: HttpClient, configs: list[dict], biases: dict,
                     strategy: dict, paper: list[dict], now: datetime,
                     errors: list[dict]) -> tuple[list[dict], list[dict]]:
    snapshots: list[dict] = []
    new_trades: list[dict] = []
    for config in configs:
        try:
            hourly = fetch_ensemble(client, config)
        except Exception as exc:
            _error(errors, config["key"], "ensemble", exc)
            continue
        today = local_date(now, config["tz"])
        for offset in range(3):
            target = today + timedelta(days=offset)
            try:
                gamma = fetch_gamma_market(client, config, target)
                if not gamma:
                    continue
                markets = add_order_books(client, gamma)
                maxima = daily_member_maxima(hourly, target.isoformat())
                bias_value = float(biases.get(config["key"], {}).get("bias", 0.0))
                corrected = [value - bias_value for value in maxima]
                definitions = [{key: market[key] for key in ("label", "lo", "hi")}
                               for market in markets]
                probabilities = bucket_probabilities(
                    corrected, definitions, config["settle_rule"], config["kernel_sigma"])
                buckets = [{**market, "p": probability}
                           for market, probability in zip(markets, probabilities)]
                snapshot = {
                    "snapshot_at": _iso(now),
                    "market": config["key"],
                    "market_name": config["name"],
                    "target_date": target.isoformat(),
                    "tz": config["tz"],
                    "unit": config["unit"],
                    "bias": bias_value,
                    **member_stats(corrected),
                    "buckets": buckets,
                }
                snapshots.append(snapshot)
                new_trades.extend(place_trades(paper, snapshot, strategy, now))
            except Exception as exc:
                _error(errors, config["key"], f"snapshot {target}", exc)
    return snapshots, new_trades


def execute(now: datetime, notify: bool = False, client: HttpClient | None = None) -> dict:
    client = client or HttpClient()
    configs = [item for item in read_json(ROOT / "config" / "markets.json", [])
               if item.get("enabled")]
    strategy = read_json(ROOT / "config" / "strategy.json", {})
    errors: list[dict] = []
    biases = read_json(DATA_DIR / "bias.json", {})
    utc_day = now.date().isoformat()
    stale = [config for config in configs
             if str(biases.get(config["key"], {}).get("updated", ""))[:10] != utc_day
             or not biases.get(config["key"], {}).get("n")]
    if stale:
        for config in stale:
            try:
                biases[config["key"]] = calculate_bias(
                    client, config, now,
                    window=int(strategy.get("bias_window_days", 10)),
                    min_days=int(strategy.get("bias_min_days", 7)))
            except Exception as exc:
                _error(errors, config["key"], "bias", exc)
                biases.setdefault(config["key"], {
                    "bias": 0.0, "resid_sd": 0.0, "n": 0,
                    "window": [], "updated": _iso(now),
                })

    results = read_json(DATA_DIR / "results.json", [])
    paper = read_json(DATA_DIR / "paper.json", [])
    historical_snapshots = iter_snapshots()
    newly_settled, pending, warnings = settle_recent(
        client, configs, now, results, historical_snapshots, errors)
    settle_trades(paper, results, float(strategy["stake"]), _iso(now))
    snapshots, new_trades = create_snapshots(
        client, configs, biases, strategy, paper, now, errors)
    summary = build_summary(results, paper, [config["key"] for config in configs], _iso(now))
    latest = {
        "generated_at": _iso(now),
        "snapshots": snapshots,
        "pending": pending,
        "errors": errors,
    }

    write_json(DATA_DIR / "bias.json", biases)
    write_json(DATA_DIR / "results.json", results)
    write_json(DATA_DIR / "paper.json", paper)
    write_json(DATA_DIR / "summary.json", summary)
    append_jsonl(DATA_DIR / "snapshots" / f"{utc_day}.jsonl", snapshots)
    write_json(DATA_DIR / "latest.json", latest)

    open_trades = sorted((trade for trade in paper if trade.get("status") == "open"),
                         key=lambda trade: (trade["target_date"], trade["market"]))
    message = build_discord_message(now, configs, newly_settled, pending, open_trades,
                                    summary, biases, errors, warnings)
    if notify:
        from scrapers.notify_discord import send
        send(message)
    return {"latest": latest, "message": message, "newly_settled": newly_settled,
            "new_trades": new_trades, "warnings": warnings}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="weatherLab v2 runner")
    parser.add_argument("--notify", action="store_true", help="send the Discord report")
    parser.add_argument("--now", help="fixed ISO timestamp (must include timezone)")
    args = parser.parse_args(argv)
    now = parse_now(args.now)
    outcome = execute(now, notify=args.notify)
    print(json.dumps({
        "generated_at": outcome["latest"]["generated_at"],
        "snapshots": len(outcome["latest"]["snapshots"]),
        "errors": outcome["latest"]["errors"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
