from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import Group, GroupMember
from bot.services.parser import parse_identity
from bot.utils.text import norm_username
from bot.utils.time import as_naive_utc, utcnow


@dataclass
class CreateGroupResult:
    group: Group
    members: list[GroupMember]
    invited: list[str] = field(default_factory=list)


@dataclass
class LeaveResult:
    ok: bool
    dissolved: bool
    new_creator_id: int | None
    group_name: str
    error: str | None = None


@dataclass
class AddResult:
    error: str | None = None
    user_id: int | None = None
    username: str | None = None
    invited_only: bool = False
    group_name: str = ""


def choose_successor(members: list[GroupMember], successor_id: int | None) -> int | None:
    if not members:
        return None
    if successor_id and any(member.user_id == successor_id for member in members):
        return successor_id
    ordered = sorted(members, key=lambda member: as_naive_utc(member.joined_at) or datetime.min)
    return ordered[0].user_id


async def create_group(
    session: AsyncSession,
    *,
    name: str,
    creator_id: int,
    creator_username: str | None,
    member_user_ids: list[int] | None = None,
    invite_usernames: list[str] | None = None,
    now: datetime | None = None,
) -> CreateGroupResult:
    now = as_naive_utc(now) or utcnow()
    group = await repo.add_group(session, name, creator_id, now)
    members = [await repo.add_member(session, group.id, creator_id, creator_username, now)]
    invited: list[str] = []
    seen_ids = {creator_id}
    for user_id in member_user_ids or []:
        if user_id in seen_ids:
            continue
        seen_ids.add(user_id)
        user = await repo.get_user(session, user_id)
        members.append(await repo.add_member(session, group.id, user_id, user.username if user else None, now))
    creator_name = norm_username(creator_username)
    for raw_name in invite_usernames or []:
        username = norm_username(raw_name)
        if not username or username == creator_name:
            continue
        existing = await repo.get_user_by_username(session, username)
        if existing:
            if existing.id not in seen_ids:
                seen_ids.add(existing.id)
                members.append(await repo.add_member(session, group.id, existing.id, existing.username, now))
            continue
        await repo.create_invite(session, group.id, username, creator_id, now)
        invited.append(username)
    return CreateGroupResult(group=group, members=members, invited=invited)


async def add_member(session: AsyncSession, group_id: int, actor_id: int, raw: str, now: datetime | None = None) -> AddResult:
    now = as_naive_utc(now) or utcnow()
    group = await repo.get_group(session, group_id)
    if group is None:
        return AddResult(error="not_found")
    if group.creator_id != actor_id:
        return AddResult(error="not_creator", group_name=group.name)
    identity = parse_identity(raw)
    if identity.user_id:
        if await repo.is_member(session, group_id, identity.user_id):
            return AddResult(error="already", user_id=identity.user_id, group_name=group.name)
        user = await repo.get_user(session, identity.user_id)
        username = user.username if user else None
        await repo.add_member(session, group_id, identity.user_id, username, now)
        return AddResult(user_id=identity.user_id, username=username, group_name=group.name)
    if identity.username:
        user = await repo.get_user_by_username(session, identity.username)
        if user:
            if await repo.is_member(session, group_id, user.id):
                return AddResult(error="already", user_id=user.id, username=user.username, group_name=group.name)
            await repo.add_member(session, group_id, user.id, user.username, now)
            return AddResult(user_id=user.id, username=user.username, group_name=group.name)
        await repo.create_invite(session, group_id, identity.username, actor_id, now)
        return AddResult(username=identity.username, invited_only=True, group_name=group.name)
    return AddResult(error="bad", group_name=group.name)


async def remove_member(session: AsyncSession, group_id: int, actor_id: int, target_id: int) -> str:
    group = await repo.get_group(session, group_id)
    if group is None:
        return "not_found"
    if group.creator_id != actor_id:
        return "not_creator"
    if target_id == actor_id:
        return "use_leave"
    if not await repo.is_member(session, group_id, target_id):
        return "not_member"
    await repo.remove_member(session, group_id, target_id)
    if group.successor_id == target_id:
        group.successor_id = None
    remaining = await repo.list_members(session, group_id)
    if not remaining:
        await repo.delete_group(session, group_id)
        return "dissolved"
    return "removed"


async def leave_group(session: AsyncSession, group_id: int, user_id: int) -> LeaveResult:
    group = await repo.get_group(session, group_id)
    if group is None:
        return LeaveResult(False, False, None, "", "not_found")
    members = await repo.list_members(session, group_id)
    if not any(member.user_id == user_id for member in members):
        return LeaveResult(False, False, None, group.name, "not_member")
    await repo.remove_member(session, group_id, user_id)
    remaining = [member for member in members if member.user_id != user_id]
    if not remaining:
        await repo.delete_group(session, group_id)
        return LeaveResult(True, True, None, group.name)
    new_creator = None
    if group.creator_id == user_id:
        new_creator = choose_successor(remaining, group.successor_id)
        group.creator_id = new_creator
        group.successor_id = None
    return LeaveResult(True, False, new_creator, group.name)


async def set_successor(session: AsyncSession, group_id: int, actor_id: int, successor_id: int) -> str:
    group = await repo.get_group(session, group_id)
    if group is None:
        return "not_found"
    if group.creator_id != actor_id:
        return "not_creator"
    if successor_id == actor_id:
        return "bad"
    if not await repo.is_member(session, group_id, successor_id):
        return "not_member"
    group.successor_id = successor_id
    return "ok"


async def transfer_now(session: AsyncSession, group_id: int, actor_id: int, successor_id: int) -> str:
    status = await set_successor(session, group_id, actor_id, successor_id)
    if status != "ok":
        return status
    group = await repo.get_group(session, group_id)
    if group is None:
        return "not_found"
    group.creator_id = successor_id
    group.successor_id = None
    return "ok"


async def rename_group(session: AsyncSession, group_id: int, actor_id: int, new_name: str) -> str:
    group = await repo.get_group(session, group_id)
    if group is None:
        return "not_found"
    if group.creator_id != actor_id:
        return "not_creator"
    cleaned = " ".join(new_name.split())
    if not cleaned or len(cleaned) > 64:
        return "bad_name"
    group.name = cleaned
    return "ok"


async def claim_invites(session: AsyncSession, user, now: datetime | None = None) -> list[Group]:
    now = as_naive_utc(now) or utcnow()
    if not user.username:
        return []
    invites = await repo.invites_for_username(session, user.username)
    joined: list[Group] = []
    for invite in invites:
        group = await repo.get_group(session, invite.group_id)
        await session.delete(invite)
        if group is None:
            continue
        if not await repo.is_member(session, group.id, user.id):
            await repo.add_member(session, group.id, user.id, user.username, now)
            joined.append(group)
    return joined


async def dissolve_if_empty(session: AsyncSession, group_id: int) -> bool:
    members = await repo.list_members(session, group_id)
    if members:
        return False
    await repo.delete_group(session, group_id)
    return True
