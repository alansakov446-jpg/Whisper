from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Contact, Group, GroupInvite, GroupMember, PendingMedia, ReadReceipt, User, Whisper
from bot.utils.text import norm_username
from bot.utils.time import as_naive_utc, utcnow


async def get_user(session: AsyncSession, user_id: int) -> User | None:
    return await session.get(User, user_id)


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    name = norm_username(username)
    if not name:
        return None
    return await session.scalar(select(User).where(func.lower(User.username) == name))


async def upsert_user(session: AsyncSession, tg_user) -> User:
    user = await session.get(User, tg_user.id)
    username = getattr(tg_user, "username", None)
    if user is None:
        lang_code = (getattr(tg_user, "language_code", None) or "").lower()
        user = User(
            id=tg_user.id,
            username=username,
            lang="en" if lang_code.startswith("en") else "ru",
            default_mode="open",
            notifications=True,
            created_at=utcnow(),
        )
        session.add(user)
        await session.flush()
        return user
    if username and username != user.username:
        user.username = username
        rows = await session.scalars(select(GroupMember).where(GroupMember.user_id == user.id))
        for row in rows.all():
            row.username = username
    return user


async def get_whisper_by_token(session: AsyncSession, token: str) -> Whisper | None:
    return await session.scalar(select(Whisper).where(Whisper.token == token))


async def count_recent_sends(session: AsyncSession, sender_hmac: str, now: datetime, window: int) -> int:
    since = now - timedelta(seconds=window)
    value = await session.scalar(
        select(func.count())
        .select_from(Whisper)
        .where(
            Whisper.sender_hmac == sender_hmac,
            Whisper.created_at >= since,
            Whisper.status.in_(("active", "scheduled", "destroyed", "delivering", "failed")),
        )
    )
    return int(value or 0)


async def list_expired(session: AsyncSession, now: datetime) -> list[Whisper]:
    rows = (
        await session.scalars(
            select(Whisper).where(
                Whisper.status == "active",
                Whisper.expires_at.is_not(None),
                Whisper.expires_at <= now,
            )
        )
    ).all()
    now_n = as_naive_utc(now)
    return [row for row in rows if as_naive_utc(row.expires_at) is not None and as_naive_utc(row.expires_at) <= now_n]


async def list_due_scheduled(session: AsyncSession, now: datetime) -> list[Whisper]:
    rows = (
        await session.scalars(
            select(Whisper).where(
                Whisper.status == "scheduled",
                Whisper.send_at <= now,
            )
        )
    ).all()
    now_n = as_naive_utc(now)
    return [row for row in rows if as_naive_utc(row.send_at) is not None and as_naive_utc(row.send_at) <= now_n]


async def list_stuck_delivering(session: AsyncSession, cutoff: datetime) -> list[Whisper]:
    rows = (await session.scalars(select(Whisper).where(Whisper.status == "delivering"))).all()
    cutoff_n = as_naive_utc(cutoff)
    return [row for row in rows if as_naive_utc(row.updated_at) is not None and as_naive_utc(row.updated_at) <= cutoff_n]


async def delete_old_drafts(session: AsyncSession, cutoff: datetime) -> int:
    result = await session.execute(delete(Whisper).where(Whisper.status == "draft", Whisper.created_at < cutoff))
    return int(result.rowcount or 0)


async def list_sent(session: AsyncSession, sender_hmac: str) -> list[Whisper]:
    rows = await session.scalars(
        select(Whisper)
        .where(
            Whisper.sender_hmac == sender_hmac,
            Whisper.status.in_(("active", "scheduled", "destroyed", "failed")),
        )
        .order_by(Whisper.created_at.desc())
    )
    return list(rows.all())


async def list_received(session: AsyncSession, user_id: int) -> list[Whisper]:
    member_groups = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    rows = await session.scalars(
        select(Whisper)
        .where(
            Whisper.status.in_(("active", "destroyed", "scheduled")),
            or_(
                Whisper.recipient_id == user_id,
                (Whisper.is_group.is_(True) & Whisper.group_id.in_(member_groups)),
            ),
        )
        .order_by(Whisper.created_at.desc())
    )
    return list(rows.all())


async def list_group_whispers(session: AsyncSession, group_id: int, limit: int = 10) -> list[Whisper]:
    rows = await session.scalars(
        select(Whisper)
        .where(
            Whisper.group_id == group_id,
            Whisper.status.in_(("active", "destroyed", "scheduled")),
        )
        .order_by(Whisper.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


async def upsert_contact(
    session: AsyncSession,
    owner_id: int,
    username: str | None,
    contact_user_id: int | None,
    now: datetime,
) -> Contact | None:
    username = norm_username(username) if username else None
    if not username and not contact_user_id:
        return None
    existing = None
    if username:
        existing = await session.scalar(
            select(Contact).where(Contact.owner_id == owner_id, Contact.username == username)
        )
    if existing is None and contact_user_id:
        existing = await session.scalar(
            select(Contact).where(Contact.owner_id == owner_id, Contact.contact_user_id == contact_user_id)
        )
    if existing:
        existing.last_used_at = now
        if username:
            existing.username = username
        if contact_user_id:
            existing.contact_user_id = contact_user_id
        return existing
    contact = Contact(
        owner_id=owner_id,
        username=username,
        contact_user_id=contact_user_id,
        last_used_at=now,
    )
    session.add(contact)
    await session.flush()
    return contact


async def list_contacts(session: AsyncSession, owner_id: int, limit: int = 20) -> list[Contact]:
    rows = await session.scalars(
        select(Contact).where(Contact.owner_id == owner_id).order_by(Contact.last_used_at.desc()).limit(limit)
    )
    return list(rows.all())


async def get_contact(session: AsyncSession, owner_id: int, contact_id: int) -> Contact | None:
    contact = await session.get(Contact, contact_id)
    if contact and contact.owner_id == owner_id:
        return contact
    return None


async def delete_contact(session: AsyncSession, owner_id: int, contact_id: int) -> bool:
    contact = await get_contact(session, owner_id, contact_id)
    if not contact:
        return False
    await session.delete(contact)
    await session.flush()
    session.expunge(contact)
    return True


async def add_group(session: AsyncSession, name: str, creator_id: int, now: datetime) -> Group:
    group = Group(name=name, creator_id=creator_id, created_at=now)
    session.add(group)
    await session.flush()
    return group


async def get_group(session: AsyncSession, group_id: int) -> Group | None:
    return await session.get(Group, group_id)


async def list_user_groups(session: AsyncSession, user_id: int) -> list[tuple[Group, int]]:
    member_ids = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    rows = await session.execute(
        select(Group, func.count(GroupMember.user_id))
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(Group.id.in_(member_ids))
        .group_by(Group.id)
        .order_by(Group.created_at.desc())
    )
    return [(group, int(count)) for group, count in rows.all()]


async def find_user_group_by_name(session: AsyncSession, user_id: int, name: str) -> Group | None:
    member_ids = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    return await session.scalar(
        select(Group)
        .where(Group.id.in_(member_ids), func.lower(Group.name) == name.strip().lower())
        .order_by(Group.created_at.desc())
    )


async def list_members(session: AsyncSession, group_id: int) -> list[GroupMember]:
    rows = await session.scalars(
        select(GroupMember).where(GroupMember.group_id == group_id).order_by(GroupMember.joined_at.asc())
    )
    return list(rows.all())


async def is_member(session: AsyncSession, group_id: int, user_id: int) -> bool:
    row = await session.get(GroupMember, (group_id, user_id))
    return row is not None


async def add_member(
    session: AsyncSession,
    group_id: int,
    user_id: int,
    username: str | None,
    now: datetime,
) -> GroupMember:
    existing = await session.get(GroupMember, (group_id, user_id))
    if existing:
        if username:
            existing.username = username
        return existing
    member = GroupMember(group_id=group_id, user_id=user_id, username=username, joined_at=now)
    session.add(member)
    await session.flush()
    return member


async def remove_member(session: AsyncSession, group_id: int, user_id: int) -> None:
    await session.execute(
        delete(GroupMember).where(GroupMember.group_id == group_id, GroupMember.user_id == user_id)
    )


async def create_invite(
    session: AsyncSession,
    group_id: int,
    username: str,
    invited_by: int,
    now: datetime,
) -> GroupInvite:
    username = norm_username(username) or username.lower()
    existing = await session.scalar(
        select(GroupInvite).where(GroupInvite.group_id == group_id, GroupInvite.username == username)
    )
    if existing:
        return existing
    invite = GroupInvite(group_id=group_id, username=username, invited_by=invited_by, created_at=now)
    session.add(invite)
    await session.flush()
    return invite


async def invites_for_username(session: AsyncSession, username: str) -> list[GroupInvite]:
    name = norm_username(username)
    if not name:
        return []
    rows = await session.scalars(select(GroupInvite).where(GroupInvite.username == name))
    return list(rows.all())


async def is_invited(session: AsyncSession, group_id: int, username: str | None) -> bool:
    name = norm_username(username)
    if not name:
        return False
    row = await session.scalar(
        select(GroupInvite.id).where(GroupInvite.group_id == group_id, GroupInvite.username == name)
    )
    return row is not None


async def delete_group(session: AsyncSession, group_id: int) -> None:
    await session.execute(delete(GroupInvite).where(GroupInvite.group_id == group_id))
    await session.execute(delete(GroupMember).where(GroupMember.group_id == group_id))
    group = await session.get(Group, group_id)
    if group is not None:
        await session.delete(group)
        await session.flush()
        session.expunge(group)


async def save_pending_media(
    session: AsyncSession,
    user_id: int,
    media_type: str,
    file_id: str,
    file_size: int | None,
    now: datetime,
) -> PendingMedia:
    row = await session.get(PendingMedia, user_id)
    if row is None:
        row = PendingMedia(
            user_id=user_id,
            media_type=media_type,
            file_id=file_id,
            file_size=file_size,
            created_at=now,
        )
        session.add(row)
    else:
        row.media_type = media_type
        row.file_id = file_id
        row.file_size = file_size
        row.created_at = now
    await session.flush()
    return row


async def get_pending_media(session: AsyncSession, user_id: int) -> PendingMedia | None:
    return await session.get(PendingMedia, user_id)


async def clear_pending_media(session: AsyncSession, user_id: int) -> None:
    row = await session.get(PendingMedia, user_id)
    if row:
        await session.delete(row)
        await session.flush()
        session.expunge(row)


async def count_groups(session: AsyncSession, user_id: int) -> int:
    value = await session.scalar(
        select(func.count()).select_from(GroupMember).where(GroupMember.user_id == user_id)
    )
    return int(value or 0)


async def count_contacts(session: AsyncSession, owner_id: int) -> int:
    value = await session.scalar(select(func.count()).select_from(Contact).where(Contact.owner_id == owner_id))
    return int(value or 0)


async def get_receipt(session: AsyncSession, whisper_id: int, user_id: int) -> ReadReceipt | None:
    return await session.get(ReadReceipt, (whisper_id, user_id))
