from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import ReadReceipt, Whisper
from bot.services.crypto import Cipher
from bot.utils.time import iso, utcnow


def _item(whisper: Whisper, *, include_text: bool) -> dict:
    return {
        "token": whisper.token,
        "text": whisper.text if include_text else None,
        "media_type": whisper.media_type if include_text else None,
        "recipient_id": whisper.recipient_id,
        "recipient_username": whisper.recipient_username,
        "group_id": whisper.group_id,
        "group_name": whisper.group_name,
        "is_anonymous": whisper.is_anonymous,
        "is_group": whisper.is_group,
        "ttl_seconds": whisper.ttl_seconds,
        "expires_at": iso(whisper.expires_at),
        "send_at": iso(whisper.send_at),
        "created_at": iso(whisper.created_at),
        "read_at": iso(whisper.read_at),
        "status": whisper.status,
        "delivery_mode": whisper.delivery_mode,
        "access": include_text and whisper.status != "destroyed",
    }


async def export_user(session: AsyncSession, user_id: int, cipher: Cipher, now: datetime | None = None) -> dict:
    now = now or utcnow()
    digest = cipher.hmac_sender(user_id)
    sent = await repo.list_sent(session, digest)
    received = await repo.list_received(session, user_id)
    sent_items = []
    for whisper in sent:
        include = True
        if whisper.is_group and whisper.group_id:
            include = await repo.is_member(session, whisper.group_id, user_id)
        if whisper.status == "destroyed":
            include = False
        item = _item(whisper, include_text=include)
        sent_items.append(item)
    received_items = []
    for whisper in received:
        if whisper.sender_hmac == digest and not whisper.recipient_id and not whisper.is_group:
            continue
        item = _item(whisper, include_text=whisper.status != "destroyed")
        if whisper.is_anonymous:
            item.pop("recipient_id", None)
            item["sender"] = None
        received_items.append(item)
    return {
        "exported_at": iso(now),
        "user_id": user_id,
        "sent": sent_items,
        "received": received_items,
    }


async def user_stats(session: AsyncSession, user_id: int, cipher: Cipher) -> dict:
    digest = cipher.hmac_sender(user_id)
    sent_rows = await repo.list_sent(session, digest)
    received = await repo.list_received(session, user_id)
    read = 0
    anonymous = 0
    active = 0
    destroyed = 0
    for whisper in sent_rows:
        if whisper.is_anonymous:
            anonymous += 1
        if whisper.status == "active":
            active += 1
        if whisper.status == "destroyed":
            destroyed += 1
        other = await session.scalar(
            select(func.count())
            .select_from(ReadReceipt)
            .where(ReadReceipt.whisper_id == whisper.id, ReadReceipt.user_id != user_id)
        )
        if other:
            read += 1
    return {
        "sent": len(sent_rows),
        "received": len(received),
        "read": read,
        "anonymous": anonymous,
        "active": active,
        "destroyed": destroyed,
        "groups": await repo.count_groups(session, user_id),
        "contacts": await repo.count_contacts(session, user_id),
    }


async def count_readable_sent(session: AsyncSession, user_id: int) -> int:
    value = await session.scalar(
        select(func.count())
        .select_from(Whisper)
        .where(or_(Whisper.sender_id == user_id, Whisper.recipient_id == user_id))
    )
    return int(value or 0)


async def export_group(session: AsyncSession, group, now: datetime | None = None) -> dict:
    """Group JSON export for its creator: meta, members, whispers."""
    from bot.db import repo
    from bot.utils.time import iso

    now = now or utcnow()
    members = await repo.list_members(session, group.id)
    whispers = await repo.list_group_whispers(session, group.id, limit=200)
    return {
        "exported_at": iso(now),
        "group": {
            "id": group.id,
            "name": group.name,
            "creator_id": group.creator_id,
            "successor_id": group.successor_id,
            "auto_join": bool(group.auto_join),
            "created_at": iso(group.created_at),
        },
        "members": [
            {
                "user_id": member.user_id,
                "username": member.username,
                "joined_at": iso(member.joined_at),
            }
            for member in members
        ],
        "whispers": [
            {
                "token": whisper.token,
                "text": whisper.text if whisper.status != "destroyed" else None,
                "media_type": whisper.media_type if whisper.status != "destroyed" else None,
                "status": whisper.status,
                "created_at": iso(whisper.created_at),
                "expires_at": iso(whisper.expires_at),
            }
            for whisper in whispers
        ],
    }
