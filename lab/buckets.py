"""Temperature bucket parsing and settlement helpers."""

from __future__ import annotations

import math
import re
from typing import Iterable


_BELOW = re.compile(r"^(-?\d+)°([FC]) or below$")
_RANGE = re.compile(r"^(-?\d+)-(-?\d+)°([FC])$")
_EXACT = re.compile(r"^(-?\d+)°([FC])$")
_HIGHER = re.compile(r"^(-?\d+)°([FC]) or higher$")


def parse_bucket(label: str) -> dict:
    """Parse one of the bucket labels used by Polymarket."""
    match = _BELOW.fullmatch(label)
    if match:
        return {"label": label, "lo": None, "hi": int(match.group(1))}
    match = _RANGE.fullmatch(label)
    if match:
        lo, hi = int(match.group(1)), int(match.group(2))
        if lo > hi:
            raise ValueError(f"invalid bucket range: {label}")
        return {"label": label, "lo": lo, "hi": hi}
    match = _EXACT.fullmatch(label)
    if match:
        value = int(match.group(1))
        return {"label": label, "lo": value, "hi": value}
    match = _HIGHER.fullmatch(label)
    if match:
        return {"label": label, "lo": int(match.group(1)), "hi": None}
    raise ValueError(f"unrecognized bucket label: {label}")


def settle_value(value: float, rule: str) -> int:
    if rule == "round":
        return math.floor(value + 0.5)
    if rule == "floor":
        return math.floor(value)
    raise ValueError(f"unknown settlement rule: {rule}")


def bucket_for_value(value: int, buckets: Iterable[dict]) -> str | None:
    for bucket in buckets:
        lo, hi = bucket["lo"], bucket["hi"]
        if (lo is None or value >= lo) and (hi is None or value <= hi):
            return bucket["label"]
    return None
