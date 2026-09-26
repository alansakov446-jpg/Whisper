from __future__ import annotations

from datetime import timedelta

import pytest

from bot.db import repo
from bot.services.access import AccessContext, check_access
from bot.services.group_service import (
    add_member,
    choose_successor,
    create_group,
    leave_group,
    remove_member,
    set_successor,
    transfer_now,
)
from bot.services.whisper_service import (
    NewWhisper,
    activate_draft,
    change_ttl,
    create_whisper,
    destroy_expired,
    is_rate_limited,
    plan_open,
    record_open,
)
from bot.utils.time import utcnow
from tests.conftest import make_user


async def _whisper(session, cipher, sender, recipient, **kwargs):
    data = NewWhisper(
        sender_id=sender.id,
        sender_username=sender.username,
        pseudonym=sender.pseudonym,
        lang=sender.lang,
        text=kwargs.pop("text", "secret"),
        recipient_id=kwargs.pop("recipient_id", recipient.id if recipient else None),
        recipient_username=kwargs.pop("recipient_username", recipient.username if recipient else None),
        default_ttl=sender.default_ttl,
        now=kwargs.pop("now", utcnow()),
        **kwargs,
    )
    return await create_whisper(session, cipher, data)


async def test_send_read_and_stranger(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    eve = await make_user(session, 3, "eve")
    whisper = await _whisper(session, cipher, alice, bob, text="только для боба")
    now = utcnow()
    assert check_access(whisper, AccessContext(bob.id, bob.username), cipher, now) == "allow"
    assert check_access(whisper, AccessContext(alice.id, alice.username), cipher, now) == "allow"
    assert check_access(whisper, AccessContext(eve.id, eve.username), cipher, now) == "deny"
    plan = plan_open(whisper, bob.id, bob.username, is_member=False, is_invited=False, cipher=cipher, now=now, lang="ru")
    assert plan.status == "ok"
    assert "только для боба" in plan.message
    await record_open(session, whisper, bob.id, bob.username, now)
    assert whisper.read_at is not None
    first = whisper.read_at
    await record_open(session, whisper, bob.id, bob.username, now + timedelta(minutes=5))
    assert whisper.read_at == first
    again = plan_open(whisper, bob.id, bob.username, is_member=False, is_invited=False, cipher=cipher, now=now, lang="ru")
    assert again.status == "ok"
    denied = plan_open(whisper, eve.id, eve.username, is_member=False, is_invited=False, cipher=cipher, now=now, lang="ru")
    assert denied.status == "deny"
    assert denied.message == "❌ Это не ваше сообщение"


async def test_ttl_expiry_and_forever(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    now = utcnow()
    short = await _whisper(
        session,
        cipher,
        alice,
        bob,
        ttl_seconds=60,
        ttl_explicit=True,
        now=now - timedelta(minutes=5),
        send_at=now - timedelta(minutes=5),
    )
    assert short.expires_at is not None
    destroyed = await destroy_expired(session, now)
    assert short in destroyed
    assert short.text is None
    assert short.status == "destroyed"
    assert short.media_file_id is None
    forever = await _whisper(session, cipher, alice, bob, text="навсегда", ttl_explicit=True, ttl_seconds=None)
    assert forever.expires_at is None
    assert await destroy_expired(session, now + timedelta(days=3650)) == [] or forever not in await destroy_expired(
        session, now
    )
    plan = plan_open(
        forever, bob.id, "bob", is_member=False, is_invited=False, cipher=cipher, now=now + timedelta(days=3000), lang="ru"
    )
    assert plan.status == "ok"
    assert "навсегда" in (plan.body or plan.message)


async def test_change_ttl_only_sender(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    whisper = await _whisper(session, cipher, alice, bob, ttl_seconds=3600, ttl_explicit=True)
    denied = await change_ttl(session, whisper, bob.id, None, cipher)
    assert denied == "denied"
    ok = await change_ttl(session, whisper, alice.id, None, cipher)
    assert ok == "ok"
    assert whisper.expires_at is None
    gone = await change_ttl(session, whisper, alice.id, 10, cipher, now=whisper.send_at + timedelta(hours=2))
    assert gone == "destroyed_now"
    assert whisper.text is None


async def test_anonymous_send_hides_sender(session, cipher):
    alice = await make_user(session, 1, "alice", pseudonym="Тень")
    bob = await make_user(session, 2, "bob")
    whisper = await _whisper(
        session,
        cipher,
        alice,
        bob,
        text="ты не узнаешь кто",
        is_anonymous=True,
        delivery_mode="anonymous",
    )
    assert whisper.sender_id is None
    assert whisper.sender_id_enc
    assert cipher.decrypt_sender(whisper.sender_id_enc) == alice.id
    now = utcnow()
    recipient = plan_open(
        whisper, bob.id, "bob", is_member=False, is_invited=False, cipher=cipher, now=now, lang="ru"
    )
    assert recipient.status == "ok"
    assert "alice" not in recipient.message.lower()
    assert "Тень" not in recipient.message
    sender = plan_open(
        whisper, alice.id, "alice", is_member=False, is_invited=False, cipher=cipher, now=now, lang="ru"
    )
    assert sender.status == "ok"
    assert "аноним" in sender.header.lower() or "ваш" in sender.header.lower()
    eve = plan_open(whisper, 9, "eve", is_member=False, is_invited=False, cipher=cipher, now=now, lang="ru")
    assert eve.status == "deny"


async def test_rate_limit(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    now = utcnow()
    for index in range(30):
        await _whisper(session, cipher, alice, bob, text=str(index), now=now)
    assert await is_rate_limited(session, cipher.hmac_sender(alice.id), now)


async def test_group_lifecycle_and_access(session, cipher):
    owner = await make_user(session, 1, "owner")
    heir = await make_user(session, 2, "heir")
    member = await make_user(session, 3, "member")
    outsider = await make_user(session, 4, "outsider")
    created = await create_group(
        session,
        name="Семья",
        creator_id=owner.id,
        creator_username=owner.username,
        member_user_ids=[heir.id, member.id],
        invite_usernames=["newbieuser"],
        now=utcnow(),
    )
    assert created.group.creator_id == owner.id
    assert any(item.user_id == member.id for item in created.members)
    assert "newbieuser" in created.invites if hasattr(created, "invites") else "newbieuser" in created.invited
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=owner.id,
            sender_username=owner.username,
            pseudonym=None,
            lang="ru",
            text="старый секрет",
            group_id=created.group.id,
            group_name=created.group.name,
            is_group=True,
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    assert (
        check_access(whisper, AccessContext(member.id, "member", is_group_member=True), cipher, utcnow()) == "allow"
    )
    removed = await remove_member(session, created.group.id, owner.id, member.id)
    assert removed == "removed"
    assert not await repo.is_member(session, created.group.id, member.id)
    assert (
        check_access(whisper, AccessContext(member.id, "member", is_group_member=False), cipher, utcnow()) == "deny"
    )
    newbie = await make_user(session, 5, "newbieuser", started_at=None)
    from bot.services.group_service import claim_invites

    joined = await claim_invites(session, newbie)
    assert joined and joined[0].id == created.group.id
    assert await repo.is_member(session, created.group.id, newbie.id)
    assert (
        check_access(whisper, AccessContext(newbie.id, "newbieuser", is_group_member=True), cipher, utcnow())
        == "allow"
    )
    status = await set_successor(session, created.group.id, owner.id, heir.id)
    assert status == "ok"
    members = await repo.list_members(session, created.group.id)
    assert choose_successor(members, heir.id) == heir.id
    left = await leave_group(session, created.group.id, owner.id)
    assert left.ok and not left.dissolved
    assert left.new_creator_id == heir.id
    group = await repo.get_group(session, created.group.id)
    assert group.creator_id == heir.id
    assert group.successor_id is None
    # transfer now
    await add_member(session, group.id, heir.id, "@outsider")
    transferred = await transfer_now(session, group.id, heir.id, outsider.id)
    assert transferred == "ok"
    group = await repo.get_group(session, group.id)
    assert group.creator_id == outsider.id
    # dissolve when the last member leaves
    for user_id in [heir.id, newbie.id]:
        if await repo.is_member(session, group.id, user_id):
            await leave_group(session, group.id, user_id)
    # outsider may still be creator; remove everyone else then leave
    for member_row in await repo.list_members(session, group.id):
        if member_row.user_id != outsider.id:
            await remove_member(session, group.id, outsider.id, member_row.user_id)
    last = await leave_group(session, group.id, outsider.id)
    assert last.dissolved
    assert await repo.get_group(session, created.group.id) is None


async def test_activate_inline_draft_creates_group(session, cipher):
    alice = await make_user(session, 1, "alice")
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="группой",
            status="draft",
            is_group=True,
            ttl_explicit=True,
            ttl_seconds=60,
            token="drafttokenvalue1",
            pending_targets={"usernames": ["bobuser"], "user_ids": [], "name": "Шёпот: @bobuser"},
        ),
    )
    result = await activate_draft(session, whisper.token, "inline-1", cipher)
    assert result.status == "activated"
    assert result.whisper.group_id is not None
    assert result.whisper.status == "active"
    assert result.whisper.inline_message_id == "inline-1"
    assert result.whisper.expires_at is not None


async def test_media_open_plan(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    whisper = await _whisper(
        session,
        cipher,
        alice,
        bob,
        text="смотри",
        media_type="photo",
        media_file_id="FILE123",
    )
    plan = plan_open(
        whisper,
        bob.id,
        "bob",
        is_member=False,
        is_invited=False,
        cipher=cipher,
        now=utcnow(),
        lang="ru",
        prefer_alert=False,
    )
    assert plan.status == "ok"
    assert plan.media_type == "photo"
    assert plan.media_file_id == "FILE123"
    assert plan.use_alert is False
