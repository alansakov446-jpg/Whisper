from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    CONVERT_RATE_COUNT,
    CONVERT_RATE_WINDOW_SEC,
    MEDIA_KEY_ALPHABET,
    MEDIA_KEY_LEN,
    MEDIA_KEY_RE,
    MEDIA_KEY_TTL_DAYS,
)
from bot.db import repo
from bot.db.models import MediaKey
from bot.utils.time import as_naive_utc, utcnow


def new_media_key() -> str:
    """6 chars from the alphabet without 0/O/1/I."""
    return "".join(secrets.choice(MEDIA_KEY_ALPHABET) for _ in range(MEDIA_KEY_LEN))


def is_valid_key(key: str | None) -> bool:
    return bool(key) and bool(MEDIA_KEY_RE.match(key or ""))


def dumps_files(files: list[dict]) -> str:
    return json.dumps(files, ensure_ascii=False)


def loads_files(raw: str | None) -> list[dict]:
    if not raw:
        return []
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if isinstance(loaded, list):
        return [item for item in loaded if isinstance(item, dict) and item.get("file_id")]
    return []


def media_group_ref(chat_id: int | None, media_group_id: str | None) -> str | None:
    if not media_group_id:
        return None
    return f"{chat_id or 0}:{media_group_id}"


def key_expires_at(now: datetime) -> datetime:
    return as_naive_utc(now) + timedelta(days=MEDIA_KEY_TTL_DAYS)


def key_alive(row: MediaKey, now: datetime) -> bool:
    expires = as_naive_utc(row.expires_at)
    now_n = as_naive_utc(now)
    if expires is not None and now_n is not None and expires <= now_n:
        return False
    return True


@dataclass
class ConvertResult:
    status: str  # ok | exists | rate_limited
    row: MediaKey | None = None
    key: str = ""


async def create_key(
    session: AsyncSession,
    *,
    owner_id: int,
    files: list[dict],
    media_group: str | None = None,
    now: datetime | None = None,
) -> ConvertResult:
    now = as_naive_utc(now) or utcnow()
    if media_group:
        existing = await repo.get_media_key_by_group(session, media_group)
        if existing is not None:
            return ConvertResult("exists", existing, existing.key)
    if await repo.count_recent_media_keys(session, owner_id, now, CONVERT_RATE_WINDOW_SEC) >= CONVERT_RATE_COUNT:
        return ConvertResult("rate_limited")
    key = new_media_key()
    while await repo.get_media_key(session, key) is not None:
        key = new_media_key()
    row = await repo.create_media_key(
        session,
        key=key,
        owner_id=owner_id,
        files=dumps_files(files),
        media_group=media_group,
        multi_use=True,
        expires_at=key_expires_at(now),
        now=now,
    )
    return ConvertResult("ok", row, key)


async def append_files(session: AsyncSession, row: MediaKey, files: list[dict]) -> MediaKey:
    current = loads_files(row.files)
    current.extend(files)
    row.files = dumps_files(current)
    await session.flush()
    return row


async def check_key(
    session: AsyncSession,
    key: str | None,
    owner_id: int,
    now: datetime | None = None,
) -> str:
    """Validate without consuming a use. ok | missing | not_yours."""
    now = as_naive_utc(now) or utcnow()
    if not is_valid_key(key):
        return "missing"
    row = await repo.get_media_key(session, key or "")
    if row is None or not key_alive(row, now):
        return "missing"
    if row.owner_id != owner_id:
        return "not_yours"
    return "ok"


async def resolve_key(
    session: AsyncSession,
    key: str | None,
    owner_id: int,
    now: datetime | None = None,
) -> str:
    """Return ok | missing | not_yours. Counts one use for single-use keys."""
    now = as_naive_utc(now) or utcnow()
    if not is_valid_key(key):
        return "missing"
    row = await repo.get_media_key(session, key or "")
    if row is None or not key_alive(row, now):
        return "missing"
    if row.owner_id != owner_id:
        return "not_yours"
    if not row.multi_use and row.use_count >= 1:
        return "missing"
    row.use_count = (row.use_count or 0) + 1
    await session.flush()
    return "ok"


async def key_files(session: AsyncSession, key: str | None, now: datetime | None = None) -> list[dict]:
    """Files behind a key as seen when opening a whisper."""
    now = as_naive_utc(now) or utcnow()
    if not is_valid_key(key):
        return []
    row = await repo.get_media_key(session, key or "")
    if row is None or not key_alive(row, now):
        return []
    return loads_files(row.files)
