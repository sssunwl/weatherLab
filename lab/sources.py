"""External data source adapters."""

from __future__ import annotations

import csv
import io
import json
from datetime import date, timedelta

from .buckets import parse_bucket
from .http import HttpClient


GAMMA_URL = "https://gamma-api.polymarket.com/events"
CLOB_URL = "https://clob.polymarket.com/book"
ENSEMBLE_URL = "https://ensemble-api.open-meteo.com/v1/ensemble"
HISTORICAL_URL = "https://historical-forecast-api.open-meteo.com/v1/forecast"
IEM_URL = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py"
HKO_DAILY_URL = "https://www.hko.gov.hk/cis/dailyExtract/dailyExtract_{year_month}.xml"
HKO_OPEN_URL = "https://data.weather.gov.hk/weatherAPI/opendata/opendata.php"

MONTHS = (
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
)


def market_slug(config: dict, target: date) -> str:
    return (f"highest-temperature-in-{config['slug_city']}-on-"
            f"{MONTHS[target.month - 1]}-{target.day}-{target.year}")


def fetch_gamma_market(client: HttpClient, config: dict, target: date) -> list[dict] | None:
    events = client.json(GAMMA_URL, params={"slug": market_slug(config, target)})
    if not isinstance(events, list):
        raise ValueError("Gamma response is not a list")
    if not events:
        return None
    markets = events[0].get("markets")
    if not isinstance(markets, list):
        raise ValueError("Gamma event has no markets list")
    parsed = []
    for market in markets:
        bucket = parse_bucket(market["groupItemTitle"])
        outcome_prices = json.loads(market["outcomePrices"])
        token_ids = json.loads(market["clobTokenIds"])
        if not outcome_prices or not token_ids:
            raise ValueError(f"Gamma market lacks YES price/token: {bucket['label']}")
        parsed.append({
            **bucket,
            "mid": float(outcome_prices[0]),
            "token_id": str(token_ids[0]),
            "closed": bool(market.get("closed")),
        })
    return parsed


def add_order_books(client: HttpClient, markets: list[dict]) -> list[dict]:
    output = []
    for market in markets:
        book = client.json(CLOB_URL, params={"token_id": market["token_id"]})
        bids = [float(item["price"]) for item in book.get("bids", [])]
        asks = [float(item["price"]) for item in book.get("asks", [])]
        output.append({
            key: value for key, value in market.items()
            if key not in {"token_id", "closed"}
        } | {
            "best_bid": max(bids) if bids else None,
            "best_ask": min(asks) if asks else None,
        })
    return output


def fetch_ensemble(client: HttpClient, config: dict) -> dict:
    unit = "fahrenheit" if config["unit"] == "F" else "celsius"
    payload = client.json(ENSEMBLE_URL, params={
        "latitude": config["lat"],
        "longitude": config["lon"],
        "hourly": "temperature_2m",
        "models": "ecmwf_ifs025,gfs025",
        "timezone": config["tz"],
        "past_days": 1,
        "forecast_days": 3,
        "temperature_unit": unit,
    })
    hourly = payload.get("hourly")
    if not isinstance(hourly, dict):
        raise ValueError("ensemble response has no hourly object")
    return hourly


def fetch_historical_forecasts(client: HttpClient, config: dict, start: date,
                               end: date) -> dict[str, float]:
    unit = "fahrenheit" if config["unit"] == "F" else "celsius"
    payload = client.json(HISTORICAL_URL, params={
        "latitude": config["lat"],
        "longitude": config["lon"],
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "daily": "temperature_2m_max",
        "models": "ecmwf_ifs025,gfs_seamless",
        "timezone": config["tz"],
        "temperature_unit": unit,
    })
    daily = payload.get("daily", {})
    times = daily.get("time", [])
    series = [values for key, values in daily.items()
              if key.startswith("temperature_2m_max")]
    if not times or not series:
        raise ValueError("historical forecast response lacks daily temperatures")
    result = {}
    for index, day in enumerate(times):
        values = [float(values[index]) for values in series
                  if index < len(values) and values[index] is not None]
        if values:
            result[day] = sum(values) / len(values)
    return result


def fetch_iem_actuals(client: HttpClient, config: dict, start: date,
                      end: date) -> dict[str, float]:
    field = "tmpf" if config["unit"] == "F" else "tmpc"
    exclusive_end = end + timedelta(days=1)
    response = client.get(IEM_URL, params={
        "station": config["iem_station"],
        "data": field,
        "year1": start.year, "month1": start.month, "day1": start.day,
        "year2": exclusive_end.year, "month2": exclusive_end.month,
        "day2": exclusive_end.day,
        "tz": config["tz"],
        "format": "onlycomma", "latlon": "no", "missing": "M", "trace": "T",
        "report_type": [3, 4],
    })
    maxima: dict[str, float] = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        day = row.get("valid", "")[:10]
        raw = row.get(field, "").strip()
        if not day or not raw or raw == "M":
            continue
        try:
            value = float(raw)
        except ValueError:
            continue
        maxima[day] = max(value, maxima.get(day, value))
    return maxima


def _hko_primary_month(client: HttpClient, year: int, month: int) -> dict[str, float]:
    payload = client.json(HKO_DAILY_URL.format(year_month=f"{year:04d}{month:02d}"))
    day_data = payload["stn"]["data"][0]["dayData"]
    values = {}
    for row in day_data:
        if len(row) <= 2 or not str(row[0]).strip().isdigit():
            continue
        try:
            value = float(str(row[2]).strip())
        except (TypeError, ValueError):
            continue
        values[f"{year:04d}-{month:02d}-{int(row[0]):02d}"] = value
    return values


def _hko_fallback_month(client: HttpClient, year: int, month: int) -> dict[str, float]:
    payload = client.json(HKO_OPEN_URL, params={
        "dataType": "CLMMAXT", "station": "HKO", "year": year, "rformat": "json",
    })
    values = {}
    for row in payload.get("data", []):
        if len(row) < 4 or int(row[1]) != month:
            continue
        try:
            value = float(str(row[3]).strip())
        except (TypeError, ValueError):
            continue
        values[f"{int(row[0]):04d}-{int(row[1]):02d}-{int(row[2]):02d}"] = value
    return values


def fetch_hko_actuals(client: HttpClient, start: date, end: date) -> dict[str, float]:
    months = []
    cursor = start.replace(day=1)
    while cursor <= end:
        months.append((cursor.year, cursor.month))
        cursor = (cursor.replace(day=28) + timedelta(days=4)).replace(day=1)
    output = {}
    for year, month in months:
        primary = {}
        try:
            primary = _hko_primary_month(client, year, month)
        except Exception:
            primary = {}
        output.update(primary)
        needed_end = min(end, (date(year + (month == 12), month % 12 + 1, 1)
                               - timedelta(days=1)))
        needed_start = max(start, date(year, month, 1))
        needed_days = (needed_end - needed_start).days + 1
        present = sum(1 for day in primary if needed_start.isoformat() <= day <= needed_end.isoformat())
        if present < needed_days:
            output.update({key: value for key, value in
                           _hko_fallback_month(client, year, month).items()
                           if key not in output})
    return output


def fetch_actuals(client: HttpClient, config: dict, start: date,
                  end: date) -> dict[str, float]:
    if config["key"] == "hong-kong":
        return fetch_hko_actuals(client, start, end)
    return fetch_iem_actuals(client, config, start, end)
