from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.services.parser import parse_identity


async def resolve_recipient(session: AsyncSession, owner_id: int, text: str) -> dict:
    raw = (text or "").strip()
    lowered = raw.lower()
    forced_group = False
    if raw.startswith("#"):
        forced_group = True
        raw = raw[1:].strip()
    elif lowered.startswith("group:"):
        forced_group = True
        raw = raw.split(":", 1)[1].strip()
    elif lowered.startswith("группа:"):
        forced_group = True
        raw = raw.split(":", 1)[1].strip()
    if forced_group:
        group = await repo.find_user_group_by_name(session, owner_id, raw)
        if group is None:
            return {"type": "invalid"}
        return {"type": "group", "group_id": group.id, "group_name": group.name}
    identity = parse_identity(raw)
    if identity.user_id:
        user = await repo.get_user(session, identity.user_id)
        return {
            "type": "user",
            "user_id": identity.user_id,
            "username": user.username if user else None,
        }
    if identity.username:
        user = await repo.get_user_by_username(session, identity.username)
        return {
            "type": "user",
            "user_id": user.id if user else None,
            "username": identity.username,
        }
    group = await repo.find_user_group_by_name(session, owner_id, raw)
    if group:
        return {"type": "group", "group_id": group.id, "group_name": group.name}
    return {"type": "invalid"}
