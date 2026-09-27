from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import (
    Contact,
    Group,
    GroupInvite,
    GroupMember,
    MediaKey,
    PendingMedia,
    Pin,
    ReadReceipt,
    User,
    Whisper,
)
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
    first_name = getattr(tg_user, "first_name", None)
    last_name = getattr(tg_user, "last_name", None)
    if user is None:
        lang_code = (getattr(tg_user, "language_code", None) or "").lower()
        user = User(
            id=tg_user.id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            lang="en" if lang_code.startswith("en") else "ru",
            default_mode="open",
            notifications=True,
            created_at=utcnow(),
        )
        session.add(user)
        await session.flush()
        return user
    changed = False
    if username and username != user.username:
        user.username = username
        changed = True
    if first_name and first_name != user.first_name:
        user.first_name = first_name
        changed = True
    if last_name and last_name != user.last_name:
        user.last_name = last_name
        changed = True
    if changed:
        rows = await session.scalars(select(GroupMember).where(GroupMember.user_id == user.id))
        for row in rows.all():
            row.username = user.username
        contacts = await session.scalars(select(Contact).where(Contact.contact_user_id == user.id))
        for row in contacts.all():
            row.username = user.username
            row.first_name = user.first_name
            row.last_name = user.last_name
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
    first_name: str | None = None,
    last_name: str | None = None,
) -> Contact | None:
    username = norm_username(username) if username else None
    if not username and not contact_user_id:
        return None
    if contact_user_id and first_name is None:
        user = await get_user(session, contact_user_id)
        if user is not None:
            username = username or user.username
            first_name = user.first_name
            last_name = user.last_name
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
        if first_name is not None:
            existing.first_name = first_name
        if last_name is not None:
            existing.last_name = last_name
        return existing
    contact = Contact(
        owner_id=owner_id,
        username=username,
        contact_user_id=contact_user_id,
        first_name=first_name,
        last_name=last_name,
        last_used_at=now,
    )
    session.add(contact)
    await session.flush()
    return contact


async def list_contacts(session: AsyncSession, owner_id: int, limit: int = 20) -> list[Contact]:
    rows = await session.scalars(
        select(Contact)
        .where(Contact.owner_id == owner_id)
        .order_by(Contact.favorite.desc(), Contact.last_used_at.desc())
        .limit(limit)
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


# --- v2: media keys ---------------------------------------------------------

async def get_media_key(session: AsyncSession, key: str) -> MediaKey | None:
    return await session.scalar(select(MediaKey).where(MediaKey.key == key))


async def get_media_key_by_group(session: AsyncSession, media_group: str) -> MediaKey | None:
    return await session.scalar(select(MediaKey).where(MediaKey.media_group == media_group))


async def list_media_keys(session: AsyncSession, owner_id: int, limit: int = 20) -> list[MediaKey]:
    rows = await session.scalars(
        select(MediaKey).where(MediaKey.owner_id == owner_id).order_by(MediaKey.created_at.desc()).limit(limit)
    )
    return list(rows.all())


async def count_recent_media_keys(session: AsyncSession, owner_id: int, now: datetime, window: int) -> int:
    since = now - timedelta(seconds=window)
    value = await session.scalar(
        select(func.count())
        .select_from(MediaKey)
        .where(MediaKey.owner_id == owner_id, MediaKey.created_at >= since)
    )
    return int(value or 0)


async def create_media_key(
    session: AsyncSession,
    *,
    key: str,
    owner_id: int,
    files: str,
    media_group: str | None = None,
    multi_use: bool = True,
    expires_at: datetime | None = None,
    now: datetime,
) -> MediaKey:
    row = MediaKey(
        key=key,
        owner_id=owner_id,
        files=files,
        media_group=media_group,
        multi_use=multi_use,
        use_count=0,
        expires_at=expires_at,
        created_at=now,
    )
    session.add(row)
    await session.flush()
    return row


async def delete_media_key(session: AsyncSession, owner_id: int, key: str) -> bool:
    row = await get_media_key(session, key)
    if row is None or row.owner_id != owner_id:
        return False
    await session.delete(row)
    await session.flush()
    session.expunge(row)
    return True


# --- v2: pins ---------------------------------------------------------------

async def add_pin(session: AsyncSession, user_id: int, whisper_id: int, now: datetime) -> Pin:
    row = await session.get(Pin, (user_id, whisper_id))
    if row is None:
        row = Pin(user_id=user_id, whisper_id=whisper_id, created_at=now)
        session.add(row)
        await session.flush()
    return row


async def remove_pin(session: AsyncSession, user_id: int, whisper_id: int) -> bool:
    row = await session.get(Pin, (user_id, whisper_id))
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    session.expunge(row)
    return True


async def is_pinned(session: AsyncSession, user_id: int, whisper_id: int) -> bool:
    return await session.get(Pin, (user_id, whisper_id)) is not None


async def list_pins(session: AsyncSession, user_id: int, limit: int = 20) -> list[tuple[Pin, Whisper]]:
    rows = await session.execute(
        select(Pin, Whisper)
        .join(Whisper, Whisper.id == Pin.whisper_id)
        .where(Pin.user_id == user_id)
        .order_by(Pin.created_at.desc())
        .limit(limit)
    )
    return [(pin, whisper) for pin, whisper in rows.all()]


# --- v2: favourites ---------------------------------------------------------

async def set_contact_favorite(session: AsyncSession, owner_id: int, contact_id: int, favorite: bool) -> bool:
    contact = await get_contact(session, owner_id, contact_id)
    if contact is None:
        return False
    contact.favorite = favorite
    return True


# --- v2: search -------------------------------------------------------------

async def search_whispers(
    session: AsyncSession,
    user_id: int,
    sender_hmac: str,
    query: str,
    limit: int = 10,
) -> list[Whisper]:
    pattern = f"%{query}%"
    member_groups = select(GroupMember.group_id).where(GroupMember.user_id == user_id)
    rows = await session.scalars(
        select(Whisper)
        .where(
            Whisper.status.in_(("active", "scheduled")),
            Whisper.text.ilike(pattern),
            or_(
                Whisper.sender_hmac == sender_hmac,
                Whisper.recipient_id == user_id,
                (Whisper.is_group.is_(True) & Whisper.group_id.in_(member_groups)),
            ),
        )
        .order_by(Whisper.created_at.desc())
        .limit(limit)
    )
    return list(rows.all())


# --- v2: group invite tokens ------------------------------------------------

async def find_group_by_invite_token(session: AsyncSession, token: str) -> Group | None:
    return await session.scalar(select(Group).where(Group.invite_token == token, Group.auto_join.is_(True)))
