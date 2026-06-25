#!/usr/bin/env python3
"""
WeatherLab Weather Forecast Scraper
Fetches ECMWF, ICON, GFS predictions from Open-Meteo API
Appends to Google Sheet Master Table
"""

import requests
import gspread
from datetime import datetime, timedelta
import json
import os
from typing import Dict, List, Optional

# City coordinates
CITIES = {
    "Tokyo": (35.6762, 139.6503),
    "Hong Kong": (22.3193, 114.1694),
    "Singapore": (1.3521, 103.8198),
    "New York City": (40.7128, -74.0060),
    "London": (51.5074, -0.1278),
}

# Open-Meteo API endpoint
API_URL = "https://api.open-meteo.com/v1/forecast"

# Sheet configuration
SHEET_ID = "1u54xWukYo5gQ49PmW6FosYNBfJOepV5o1yaF6_o1Z7M"
SHEET_NAME = "Master Table"


def fetch_weather_forecast(lat: float, lon: float) -> Dict:
    """
    Fetch weather forecast from Open-Meteo API
    Returns predictions for today and tomorrow
    """
    params = {
        "latitude": lat,
        "longitude": lon,
        "temperature_2m": "true",
        "models": "ecmwf_ifs025,icon_seamless,gfs025",
        "forecast_days": 2,
        "timezone": "UTC",
    }

    try:
        response = requests.get(API_URL, params=params, timeout=10)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error fetching API: {e}")
        return {}


def extract_temps(forecast_data: Dict, forecast_days: int = 2) -> List[Dict]:
    """
    Extract temperature predictions from API response
    Returns list of {date, ecmwf_high, ecmwf_low, icon_high, icon_low, gfs_high, gfs_low}
    """
    if not forecast_data or "hourly" not in forecast_data:
        return []

    hourly = forecast_data["hourly"]
    temps_by_model = {}

    # Parse hourly data grouped by model
    for key in hourly.keys():
        if "_temperature_2m" in key:
            model_name = key.split("_temperature_2m")[0]
            temps_by_model[model_name] = hourly[key]

    if not temps_by_model:
        return []

    # Extract daily high/low for each model
    results = []
    time_data = hourly.get("time", [])

    for day_offset in range(forecast_days):
        if day_offset >= len(time_data):
            break

        day_str = time_data[day_offset][:10]  # YYYY-MM-DD
        day_temps = {}

        for model_name, temps in temps_by_model.items():
            day_range = temps[day_offset * 24:(day_offset + 1) * 24]
            if day_range:
                high = max(day_range)
                low = min(day_range)
                day_temps[f"{model_name}_high"] = round(high, 1)
                day_temps[f"{model_name}_low"] = round(low, 1)

        if day_temps:
            day_temps["date"] = day_str
            results.append(day_temps)

    return results


def get_sheet_client():
    """
    Authenticate and return gspread client
    Uses GOOGLE_SHEETS_CREDS environment variable (JSON string)
    """
    try:
        creds_json = os.getenv("GOOGLE_SHEETS_CREDS")
        if not creds_json:
            print("Error: GOOGLE_SHEETS_CREDS environment variable not set")
            return None

        creds_dict = json.loads(creds_json)
        gc = gspread.service_account_from_dict(creds_dict)
        return gc
    except Exception as e:
        print(f"Error authenticating with Google Sheets: {e}")
        return None


def append_to_sheet(client: gspread.Client, data_rows: List[List]):
    """
    Append data rows to Google Sheet
    """
    try:
        sheet = client.open_by_key(SHEET_ID)
        worksheet = sheet.worksheet(SHEET_NAME)

        # Append rows
        worksheet.append_rows(data_rows, value_input_option="USER_ENTERED")
        print(f"✓ Appended {len(data_rows)} rows to sheet")

    except Exception as e:
        print(f"Error appending to sheet: {e}")


def format_sheet_row(city: str, snapshot_time: str, forecast_data: Dict) -> Optional[List]:
    """
    Format data into a sheet row
    Row format: [snapshot_time, forecast_date, city, ecmwf_high, icon_high, gfs_high, ecmwf_low, icon_low, gfs_low, ...]
    """
    if not forecast_data:
        return None

    temps = extract_temps(forecast_data, forecast_days=2)
    if not temps:
        return None

    # Use tomorrow's forecast (index 1, since index 0 is today)
    if len(temps) < 2:
        forecast_temps = temps[0] if temps else {}
    else:
        forecast_temps = temps[1]

    forecast_date = forecast_temps.get("date")
    if not forecast_date:
        return None

    row = [
        snapshot_time,
        forecast_date,
        city,
        forecast_temps.get("ecmwf_ifs025_high", ""),
        forecast_temps.get("icon_seamless_high", ""),
        forecast_temps.get("gfs025_high", ""),
        forecast_temps.get("ecmwf_ifs025_low", ""),
        forecast_temps.get("icon_seamless_low", ""),
        forecast_temps.get("gfs025_low", ""),
        "",  # actual_high (to be filled later)
        "",  # actual_low (to be filled later)
    ]

    return row


def main():
    """
    Main execution: fetch forecasts for all cities and append to sheet
    """
    # Get current timestamp (JST)
    jst_offset = 9  # JST is UTC+9
    now_utc = datetime.utcnow()
    now_jst = now_utc + timedelta(hours=jst_offset)
    snapshot_time = now_jst.strftime("%Y-%m-%d %H:%M JST")

    print(f"Starting scraper: {snapshot_time}")

    # Fetch data for all cities
    all_rows = []
    for city, (lat, lon) in CITIES.items():
        print(f"  Fetching {city}...")
        forecast_data = fetch_weather_forecast(lat, lon)
        row = format_sheet_row(city, snapshot_time, forecast_data)
        if row:
            all_rows.append(row)

    # Append to sheet
    if all_rows:
        client = get_sheet_client()
        if client:
            append_to_sheet(client, all_rows)
            print(f"✓ Successfully scraped {len(all_rows)} cities")
        else:
            print("! Could not authenticate with Google Sheets")
    else:
        print("! No data to append")


if __name__ == "__main__":
    main()
