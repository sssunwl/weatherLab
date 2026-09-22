"""Timezone-aware run and settlement timing."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo


def parse_now(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("--now must include a timezone")
    return parsed.astimezone(timezone.utc)


def local_date(now: datetime, tz_name: str) -> date:
    return now.astimezone(ZoneInfo(tz_name)).date()


def local_midnight_utc(day: date, tz_name: str) -> datetime:
    return datetime.combine(day, time.min, ZoneInfo(tz_name)).astimezone(timezone.utc)


def ready_to_settle(target: date, tz_name: str, now: datetime) -> bool:
    next_midnight = datetime.combine(target + timedelta(days=1), time.min,
                                     ZoneInfo(tz_name))
    return now >= next_midnight.astimezone(timezone.utc) + timedelta(hours=2)
