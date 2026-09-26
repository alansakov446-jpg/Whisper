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
