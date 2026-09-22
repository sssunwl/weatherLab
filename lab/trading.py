"""Paper trade placement and resolution."""

from __future__ import annotations

import hashlib
from datetime import date, datetime

from .timeutil import local_date


def place_trades(paper: list[dict], snapshot: dict, strategy: dict,
                 now: datetime) -> list[dict]:
    target = date.fromisoformat(snapshot["target_date"])
    if target <= local_date(now, snapshot["tz"]):
        return []
    existing = {(trade["market"], trade["target_date"], trade["bucket"])
                for trade in paper}
    created = []
    for bucket in snapshot["buckets"]:
        ask = bucket.get("best_ask")
        if ask is None:
            continue
        edge = bucket["p"] - ask
        key = (snapshot["market"], snapshot["target_date"], bucket["label"])
        if (edge + 1e-12 < strategy["min_edge"] or ask < strategy["min_ask"]
                or ask > strategy["max_ask"] or key in existing):
            continue
        raw_id = "|".join(key).encode("utf-8")
        trade = {
            "id": hashlib.sha256(raw_id).hexdigest()[:16],
            "market": snapshot["market"],
            "target_date": snapshot["target_date"],
            "bucket": bucket["label"],
            "p": bucket["p"],
            "best_ask": ask,
            "edge": edge,
            "shares": strategy["stake"] / ask,
            "placed_at": now.isoformat().replace("+00:00", "Z"),
            "status": "open",
            "pnl": None,
        }
        paper.append(trade)
        created.append(trade)
        existing.add(key)
    return created


def settle_trades(paper: list[dict], results: list[dict], stake: float,
                  settled_at: str) -> None:
    actuals = {(row["market"], row["target_date"]):
               row.get("market_bucket") or row["our_bucket"]
               for row in results}
    for trade in paper:
        if trade.get("status") != "open":
            continue
        actual = actuals.get((trade["market"], trade["target_date"]))
        if actual is None:
            continue
        won = trade["bucket"] == actual
        trade["status"] = "won" if won else "lost"
        trade["pnl"] = trade["shares"] - stake if won else -stake
        trade["settled_at"] = settled_at
