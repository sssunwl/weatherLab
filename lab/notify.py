"""Discord daily report formatting."""

from __future__ import annotations

from datetime import datetime


DASHBOARD_URL = "https://sssunwl.github.io/weatherLab/"


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{max(0.001, min(0.999, value)):.0%}"


def _money(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}${abs(value):.0f}"


def build_discord_message(now: datetime, configs: list[dict], newly_settled: list[dict],
                          pending: list[dict], new_trades: list[dict], summary: dict,
                          biases: dict, errors: list[dict], warnings: list[str]) -> str:
    names = {config["key"]: config["name"] for config in configs}
    units = {config["key"]: config["unit"] for config in configs}
    lines = [f"🌡 weatherLab {now.month}/{now.day}", "━ 昨日結算 ━"]
    if not newly_settled and not pending:
        lines.append("今天沒有新結算")
    for row in newly_settled:
        unit = units[row["market"]]
        probability = row.get("p_of_actual")
        if row.get("hit") is None:
            verdict = "尚無可評分快照"
        elif row["hit"]:
            verdict = "✅ 最看好那格"
        else:
            verdict = f"❌ 最看好 {row['top_bucket']} ({_pct(row['top_p'])})"
        market_mid = row.get("market_mids", {}).get(row["our_bucket"])
        lines.append(f"{names[row['market']]}   實測 {row['obs_value']}°{unit} → "
                     f"{row['our_bucket']}  我們給 {_pct(probability)} {verdict}  "
                     f"市價 {_pct(market_mid)}")
    for item in pending:
        lines.append(f"{names[item['market']]} ⏳ {item['reason']}")

    threshold = 8
    lines.append(f"━ 明日機會(edge ≥ {threshold}%)━")
    if not new_trades:
        lines.append("今天沒有值得下的")
    for trade in new_trades:
        short_name = names[trade["market"]].split()[0]
        month_day = trade["target_date"][5:].replace("-", "/")
        lines.append(f"{short_name} {month_day} {trade['bucket']}  "
                     f"我們 {_pct(trade['p'])} / 賣價 {_pct(trade['best_ask'])}  "
                     f"edge {trade['edge']:+.0%}  [模擬單]")

    lines.append("━ 累計(紙上,最近 30 天)━")
    scores = summary["all"]
    paper = summary["paper"]
    cumulative = (f"命中 {scores['hits']}/{scores['n']} · Brier "
                  f"{scores['brier']:.2f}" if scores["brier"] is not None
                  else f"命中 {scores['hits']}/{scores['n']} · Brier —")
    market_brier = (f"{scores['market_brier']:.2f}"
                    if scores["market_brier"] is not None else "—")
    cumulative += (f"(市價 {market_brier}) · 模擬 {paper['trades']} 筆 "
                   f"勝 {paper['wins']} · 損益 {_money(paper['pnl'])}")
    if scores["n"] < 30:
        cumulative += f" (樣本 {scores['n']} 天,還不能下結論)"
    lines.append(cumulative)

    for config in configs:
        item = biases.get(config["key"], {})
        if item.get("n", 0) < 20:
            lines.append(f"⚠️ {config['name']} 偏差樣本不足")
    lines.extend(f"⚠️ {warning}" for warning in warnings)
    failed = sorted({item["market"] for item in errors})
    if failed:
        lines.append("⚠️ 失敗市場: " + "、".join(names.get(key, key) for key in failed))
    lines.append(f"儀表板 {DASHBOARD_URL}")
    return "```\n" + "\n".join(lines) + "\n```"
