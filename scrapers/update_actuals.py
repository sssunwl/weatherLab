#!/usr/bin/env python3
"""
WeatherLab Actual Temperature Updater
Fills in actual_high/actual_low for past forecast_date rows
using Open-Meteo Archive API
"""

import requests
import gspread
from datetime import datetime, timedelta
from typing import Dict, Optional

CITIES = {
    "Tokyo": (35.6762, 139.6503),
    "Hong Kong": (22.3193, 114.1694),
    "Singapore": (1.3521, 103.8198),
    "New York City": (40.7128, -74.0060),
    "London": (51.5074, -0.1278),
}

ARCHIVE_API_URL = "https://archive-api.open-meteo.com/v1/archive"
SHEET_ID = "1u54xWukYo5gQ49PmW6FosYNBfJOepV5o1yaF6_o1Z7M"
SHEET_NAME = "Master Table"

# Column indices (0-based) in Master Table
COL_FORECAST_DATE = 1
COL_CITY = 2
COL_ACTUAL_HIGH = 9
COL_ACTUAL_LOW = 10


def fetch_actual_temps(lat: float, lon: float, date_str: str) -> Optional[Dict]:
    """
    Fetch actual high/low temperature for a given date
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": date_str,
        "end_date": date_str,
        "hourly": "temperature_2m",
    }

    try:
        response = requests.get(ARCHIVE_API_URL, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        temps = data.get("hourly", {}).get("temperature_2m", [])
        temps = [t for t in temps if t is not None]

        if not temps:
            return None

        return {
            "high": round(max(temps)),
            "low": round(min(temps)),
        }
    except Exception as e:
        print(f"Error fetching actuals for {date_str}: {e}")
        return None


def get_sheet_client():
    try:
        gc = gspread.oauth(
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
        return gc
    except Exception as e:
        print(f"Error authenticating: {e}")
        return None


def main():
    client = get_sheet_client()
    if not client:
        print("! Could not authenticate with Google Sheets")
        return

    sheet = client.open_by_key(SHEET_ID)
    worksheet = sheet.worksheet(SHEET_NAME)
    rows = worksheet.get_all_values()

    today = (datetime.utcnow() + timedelta(hours=9)).date()  # JST
    updates = []  # list of (row_number, col_number, value)

    print(f"Scanning {len(rows) - 1} rows for missing actuals...")

    # Cache fetched actuals per (city, date) to avoid duplicate API calls
    actuals_cache = {}

    for row_idx, row in enumerate(rows[1:], start=2):  # skip header, 1-indexed + header offset
        if len(row) <= COL_ACTUAL_HIGH:
            continue

        forecast_date_str = row[COL_FORECAST_DATE].strip()
        city = row[COL_CITY].strip()
        actual_high = row[COL_ACTUAL_HIGH].strip() if len(row) > COL_ACTUAL_HIGH else ""
        actual_low = row[COL_ACTUAL_LOW].strip() if len(row) > COL_ACTUAL_LOW else ""

        if not forecast_date_str or not city or city not in CITIES:
            continue

        if actual_high and actual_low:
            continue  # already filled

        try:
            forecast_date = datetime.strptime(forecast_date_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if forecast_date >= today:
            continue  # only backfill past dates (today's data is incomplete)

        cache_key = (city, forecast_date_str)
        if cache_key not in actuals_cache:
            lat, lon = CITIES[city]
            print(f"  Fetching actual temps: {city} / {forecast_date_str}...")
            actuals_cache[cache_key] = fetch_actual_temps(lat, lon, forecast_date_str)

        actual_data = actuals_cache[cache_key]
        if actual_data:
            if not actual_high:
                updates.append((row_idx, COL_ACTUAL_HIGH + 1, actual_data["high"]))
            if not actual_low:
                updates.append((row_idx, COL_ACTUAL_LOW + 1, actual_data["low"]))

    if updates:
        print(f"Applying {len(updates)} cell updates...")
        cell_updates = [
            gspread.Cell(row=r, col=c, value=v) for r, c, v in updates
        ]
        worksheet.update_cells(cell_updates, value_input_option="USER_ENTERED")
        print(f"✓ Updated {len(updates)} cells")
    else:
        print("No missing actuals to update")


if __name__ == "__main__":
    main()
