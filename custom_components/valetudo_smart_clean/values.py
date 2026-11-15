"""Tolerant parsers shared by the options flow and the runtime.

The options flow validates input through selectors, so it sees clean values.
The runtime may read an options document edited by hand or migrated from an
older version; there a bad value falls back to the default instead of raising.
"""

from __future__ import annotations

from datetime import time
from typing import Any


def parse_time(value: Any, default: time) -> time:
    if isinstance(value, time):
        return value
    if isinstance(value, str):
        try:
            return time.fromisoformat(value)
        except ValueError:
            return default
    return default


def parse_weekdays(value: Any, default: frozenset[int]) -> frozenset[int]:
    if value is None:
        return default
    try:
        return frozenset(int(day) for day in value)
    except (TypeError, ValueError):
        return default
