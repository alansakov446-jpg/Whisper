from __future__ import annotations

import re
from datetime import datetime, timedelta

from bot.constants import MAX_TTL_SECONDS, MIN_TTL_SECONDS

FOREVER_WORDS = {
    "forever",
    "inf",
    "infinity",
    "permanent",
    "never",
    "навсегда",
    "бессрочно",
    "∞",
    "♾",
}

UNITS = {
    "s": 1,
    "sec": 1,
    "secs": 1,
    "second": 1,
    "seconds": 1,
    "с": 1,
    "сек": 1,
    "секунда": 1,
    "секунды": 1,
    "секунд": 1,
    "m": 60,
    "min": 60,
    "mins": 60,
    "minute": 60,
    "minutes": 60,
    "м": 60,
    "мин": 60,
    "минута": 60,
    "минуты": 60,
    "минут": 60,
    "h": 3600,
    "hr": 3600,
    "hrs": 3600,
    "hour": 3600,
    "hours": 3600,
    "ч": 3600,
    "час": 3600,
    "часа": 3600,
    "часов": 3600,
    "d": 86400,
    "day": 86400,
    "days": 86400,
    "д": 86400,
    "день": 86400,
    "дня": 86400,
    "дней": 86400,
    "w": 604800,
    "week": 604800,
    "weeks": 604800,
    "н": 604800,
    "нед": 604800,
    "неделя": 604800,
    "недели": 604800,
    "недель": 604800,
}

NOW_WORDS = {"now", "сейчас", "сразу", "skip", "-", "0"}


class DurationError(ValueError):
    pass


def parse_duration(text: str, *, allow_forever: bool = True) -> int | None:
    raw = (text or "").strip().lower().replace("ё", "е")
    if not raw:
        raise DurationError("empty")
    if allow_forever and raw in FOREVER_WORDS:
        return None
    compact = raw.replace(" ", "")
    matches = re.findall(r"(\d+)([a-zа-я]+)", compact)
    rest = re.sub(r"(\d+)([a-zа-я]+)", "", compact)
    if matches and rest == "":
        total = 0
        for num, unit in matches:
            if unit not in UNITS:
                raise DurationError(unit)
            total += int(num) * UNITS[unit]
        if total <= 0:
            raise DurationError("nonpositive")
        return total
    if re.fullmatch(r"\d+", compact):
        value = int(compact)
        if value <= 0:
            raise DurationError("nonpositive")
        return value
    raise DurationError(raw)


def validate_ttl(seconds: int | None) -> int | None:
    if seconds is None:
        return None
    if seconds < MIN_TTL_SECONDS or seconds > MAX_TTL_SECONDS:
        raise DurationError("ttl out of range")
    return seconds


def resolve_ttl(*, explicit: bool, chosen: int | None, default: int | None) -> int | None:
    """Priority: per-whisper choice, then settings default, then forever."""
    if explicit:
        return chosen
    return default


def compute_expires(send_at: datetime, ttl_seconds: int | None) -> datetime | None:
    if ttl_seconds is None:
        return None
    return send_at + timedelta(seconds=ttl_seconds)


def parse_schedule(text: str, now: datetime) -> datetime:
    raw = (text or "").strip()
    low = raw.lower().replace("ё", "е")
    if low in NOW_WORDS:
        return now
    relative = low
    for prefix in ("через ", "in ", "after "):
        if relative.startswith(prefix):
            relative = relative[len(prefix) :]
            break
    try:
        seconds = parse_duration(relative, allow_forever=False)
    except DurationError:
        seconds = None
    if seconds is not None:
        if seconds < MIN_TTL_SECONDS:
            raise DurationError("too soon")
        return now + timedelta(seconds=seconds)
    for fmt in (
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d %H:%M:%S",
        "%d.%m.%Y %H:%M",
        "%d.%m.%Y %H:%M:%S",
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    raise DurationError(raw)
