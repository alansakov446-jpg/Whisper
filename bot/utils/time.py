from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC. Every timestamp in the database is UTC without tzinfo."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_naive_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def iso(value: datetime | None) -> str | None:
    value = as_naive_utc(value)
    if value is None:
        return None
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def format_human(value: datetime | None) -> str:
    value = as_naive_utc(value)
    if value is None:
        return "—"
    return value.strftime("%d.%m.%Y %H:%M UTC")


def parse_hhmm(value: str | None) -> int | None:
    """'23:30' -> minutes since midnight. None when invalid."""
    raw = (value or "").strip()
    if not raw:
        return None
    parts = raw.split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        return None
    hours, minutes = int(parts[0]), int(parts[1])
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def in_quiet_hours(now: datetime, quiet_from: str | None, quiet_to: str | None) -> bool:
    """True when `now` (UTC) falls into the quiet range. Range may wrap midnight."""
    start = parse_hhmm(quiet_from)
    end = parse_hhmm(quiet_to)
    if start is None or end is None or start == end:
        return False
    current = as_naive_utc(now)
    if current is None:
        return False
    minute = current.hour * 60 + current.minute
    if start < end:
        return start <= minute < end
    return minute >= start or minute < end
