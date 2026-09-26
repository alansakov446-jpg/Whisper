from __future__ import annotations

from types import SimpleNamespace

import pytest

from bot.db import repo
from bot.handlers.groups import cmd_cgn, on_group, rename_text, add_text
from bot.handlers.inline import on_chosen, on_inline
from bot.handlers.misc import cmd_cancel, cmd_export, cmd_nomedia, cmd_stats, delete_contact_cb, menu_contacts, on_error, save_media
from bot.handlers.send import (
    cmd_send,
    content_media,
    content_text,
    pick_mode,
    pick_recipient,
    pick_ttl,
    sched_now,
    sched_text,
    send_cancel,
    send_ok,
    skip_text,
    text_recipient,
    ttl_text,
    use_saved_media,
)
from bot.handlers.settings import (
    ask_pseudo,
    cmd_settings,
    custom_default_ttl,
    menu_settings,
    save_pseudo,
    set_lang,
    set_mode,
    set_notif,
    set_ttl,
)
from bot.handlers.start import cmd_help, cmd_menu, cmd_start, menu_help, menu_main
from bot.handlers.whispers import cb_open, cb_page, cb_ttlset, ttl_custom_text
from bot.services.whisper_service import NewWhisper, create_whisper
from bot.utils.time import utcnow
from tests.conftest import make_user
from tests.fakes import FakeBot, FakeCallback, FakeChosen, FakeMessage, FakeQuery, FakeState


def command(args=None):
    return SimpleNamespace(args=args)


async def test_start_deeplink_ttl_group_and_menu(session, cipher):
    alice = await make_user(session, 1, "alice", started_at=None)
    bob = await make_user(session, 2, "bob")
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym="Тень",
            lang="ru",
            text="deeplink secret",
            recipient_id=bob.id,
            recipient_username="bob",
            ttl_explicit=True,
            ttl_seconds=None,
            token="deeplinktokenvalue",
            media_type="photo",
            media_file_id="FILE1",
        ),
    )
    bot = FakeBot()
    state = FakeState()
    message = FakeMessage(2, username="bob")
    await cmd_start(message, command(f"w_{whisper.token}"), session, bob, cipher, bot, "WhisperBot", state)
    assert bob.started_at is not None
    assert bot.media and bot.media[0][0] == "photo"

    stranger = FakeMessage(3, username="eve")
    eve = await make_user(session, 3, "eve", started_at=None)
    await cmd_start(stranger, command(f"w_{whisper.token}"), session, eve, cipher, bot, "WhisperBot", FakeState())
    assert any("не ваше" in item for item in stranger.answers)

    missing = FakeMessage(1)
    await cmd_start(missing, command("w_missingtokenvalue"), session, alice, cipher, bot, "WhisperBot", FakeState())
    assert any("недействительна" in item or "не найдено" in item for item in missing.answers)

    ttl_msg = FakeMessage(1)
    await cmd_start(ttl_msg, command(f"ttl_{whisper.token}"), session, alice, cipher, bot, "WhisperBot", FakeState())
    assert ttl_msg.answers

    denied = FakeMessage(2, username="bob")
    await cmd_start(denied, command(f"ttl_{whisper.token}"), session, bob, cipher, bot, "WhisperBot", FakeState())
    assert any("отправитель" in item for item in denied.answers)

    from bot.services.group_service import create_group

    created = await create_group(session, name="Семья", creator_id=alice.id, creator_username="alice")
    group_msg = FakeMessage(1)
    await cmd_start(group_msg, command(f"grp_{created.group.id}"), session, alice, cipher, bot, "WhisperBot", FakeState())
    assert any("Семья" in item for item in group_msg.answers)
    outsider = FakeMessage(3, username="eve")
    await cmd_start(outsider, command(f"grp_{created.group.id}"), session, eve, cipher, bot, "WhisperBot", FakeState())
    assert any("не ваша" in item for item in outsider.answers)
    gone = FakeMessage(1)
    await cmd_start(gone, command("grp_999"), session, alice, cipher, bot, "WhisperBot", FakeState())
    assert any("не найдена" in item for item in gone.answers)

    unknown = FakeMessage(1)
    await cmd_start(unknown, command("weird"), session, alice, cipher, bot, "WhisperBot", FakeState())
    assert any("Не распознал" in item or "Инструкция" in item or "Inline" in item for item in unknown.answers)
    help_msg = FakeMessage(1)
    await cmd_help(help_msg, alice, "WhisperBot", FakeState())
    assert help_msg.answers
    menu = FakeMessage(1, chat_type="group")
    await cmd_menu(menu, alice, FakeState())
    assert any("личке" in item for item in menu.answers)
    private_menu = FakeMessage(1)
    await cmd_menu(private_menu, alice, FakeState())
    assert private_menu.answers
    cb = FakeCallback("menu:main", message=FakeMessage(1))
    await menu_main(cb, alice)
    help_cb = FakeCallback("menu:help", message=FakeMessage(1))
    await menu_help(help_cb, alice, "WhisperBot")
    assert help_cb.message.answers


async def test_inline_and_open_flows(session, cipher):
    alice = await make_user(session, 1, "alice", pseudonym="Тень")
    bob = await make_user(session, 2, "bobby")
    query = FakeQuery("привет @bobby ttl:1h")
    await on_inline(query, session, alice, cipher, "WhisperBot")
    assert query.results
    token = query.results[0].id
    chosen = FakeChosen(token, "inline-msg")
    bot = FakeBot()
    await on_chosen(chosen, session, alice, cipher, bot)
    whisper = await repo.get_whisper_by_token(session, token)
    assert whisper.status == "active"
    assert whisper.inline_message_id == "inline-msg"

    cold = await make_user(session, 8, "cold", started_at=None)
    inactive = FakeQuery("hi @bob")
    await on_inline(inactive, session, cold, cipher, "WhisperBot")
    assert inactive.kwargs["button"] is not None

    empty = FakeQuery("")
    await on_inline(empty, session, None, cipher, "WhisperBot")
    assert empty.results == []

    cb = FakeCallback(f"open:{token}", user_id=2, username="bob")
    await cb_open(cb, session, bob, cipher, bot, "WhisperBot")
    assert cb.alerts
    eve = await make_user(session, 9, "eve")
    denied = FakeCallback(f"open:{token}", user_id=9, username="eve")
    await cb_open(denied, session, eve, cipher, bot, "WhisperBot")
    assert any(text and "не ваше" in text for text, _alert in denied.alerts)
    missing = FakeCallback("open:nope-token-here", user_id=1)
    await cb_open(missing, session, alice, cipher, bot, "WhisperBot")
    assert missing.alerts

    long = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="я" * 4000,
            recipient_id=bob.id,
            recipient_username="bob",
            ttl_explicit=True,
            ttl_seconds=None,
            token="longtokenvalue0001",
        ),
    )
    page = FakeCallback(f"pg:{long.token}:1", user_id=2, username="bob", message=FakeMessage(2, username="bob"))
    await cb_page(page, session, bob, cipher, bot)
    assert page.message.answers or page.alerts

    ttl = FakeCallback(f"ttlset:{whisper.token}:3600", user_id=1, message=FakeMessage(1))
    await cb_ttlset(ttl, session, alice, cipher, bot, "WhisperBot", FakeState())
    assert whisper.ttl_seconds == 3600
    stranger_ttl = FakeCallback(f"ttlset:{whisper.token}:60", user_id=2, username="bob")
    await cb_ttlset(stranger_ttl, session, bob, cipher, bot, "WhisperBot", FakeState())
    custom = FakeCallback(f"ttlset:{whisper.token}:custom", user_id=1, message=FakeMessage(1))
    state = FakeState()
    await cb_ttlset(custom, session, alice, cipher, bot, "WhisperBot", state)
    msg = FakeMessage(1, text="2h")
    await ttl_custom_text(msg, state, session, alice, cipher, bot, "WhisperBot")
    assert whisper.ttl_seconds == 7200
    bad = FakeMessage(1, text="nope")
    state2 = FakeState()
    await state2.update_data(token=whisper.token)
    await ttl_custom_text(bad, state2, session, alice, cipher, bot, "WhisperBot")
    assert any("Не понял" in item for item in bad.answers)


async def test_settings_and_misc(session, cipher):
    alice = await make_user(session, 1, "alice")
    state = FakeState()
    message = FakeMessage(1)
    await cmd_settings(message, alice, state)
    assert message.answers
    groupish = FakeMessage(1, chat_type="group")
    await cmd_settings(groupish, alice, FakeState())
    assert any("личке" in item for item in groupish.answers)
    cb = FakeCallback("set:ttl:3600", message=FakeMessage(1))
    await set_ttl(cb, alice, FakeState())
    assert alice.default_ttl == 3600
    custom = FakeCallback("set:ttl:custom", message=FakeMessage(1))
    st = FakeState()
    await set_ttl(custom, alice, st)
    saved = FakeMessage(1, text="2d")
    await custom_default_ttl(saved, st, alice)
    assert alice.default_ttl == 2 * 86400
    bad = FakeMessage(1, text="zzz")
    await custom_default_ttl(bad, st, alice)
    await set_mode(FakeCallback("set:mode:anonymous"), alice)
    assert alice.default_mode == "anonymous"
    await set_notif(FakeCallback("set:notif"), alice)
    assert alice.notifications is False
    await set_lang(FakeCallback("set:lang:en"), alice)
    assert alice.lang == "en"
    alice.lang = "ru"
    pseudo_state = FakeState()
    await ask_pseudo(FakeCallback("set:pseudo", message=FakeMessage(1)), alice, pseudo_state)
    await save_pseudo(FakeMessage(1, text="Тень"), pseudo_state, alice)
    assert alice.pseudonym == "Тень"
    await save_pseudo(FakeMessage(1, text="-"), FakeState(), alice)
    assert alice.pseudonym is None
    await menu_settings(FakeCallback("menu:settings", message=FakeMessage(1)), alice, FakeState())
    await cmd_stats(FakeMessage(1), session, alice, cipher, FakeState())
    await cmd_export(FakeMessage(1), session, alice, cipher, FakeState())
    await repo.save_pending_media(session, alice.id, "photo", "F", 10, utcnow())
    await cmd_nomedia(FakeMessage(1), session, alice)
    assert await repo.get_pending_media(session, alice.id) is None
    await cmd_nomedia(FakeMessage(1), session, alice)
    await cmd_cancel(FakeMessage(1), FakeState(), alice)
    contacts_cb = FakeCallback("menu:contacts", message=FakeMessage(1))
    await menu_contacts(contacts_cb, session, alice)
    await repo.upsert_contact(session, alice.id, "bob", 2, utcnow())
    listed = FakeCallback("menu:contacts", message=FakeMessage(1))
    await menu_contacts(listed, session, alice)
    contact = (await repo.list_contacts(session, alice.id))[0]
    await delete_contact_cb(FakeCallback(f"contact:del:{contact.id}", message=FakeMessage(1)), session, alice)
    photo = FakeMessage(1)
    photo.photo = [SimpleNamespace(file_id="pic", file_size=100)]
    await save_media(photo, session, alice, "WhisperBot")
    assert await repo.get_pending_media(session, alice.id)
    huge = FakeMessage(1)
    huge.photo = [SimpleNamespace(file_id="big", file_size=21 * 1024 * 1024)]
    await save_media(huge, session, alice, "WhisperBot")
    assert any("20" in item for item in huge.answers)
    public = FakeMessage(1, chat_type="group")
    public.photo = [SimpleNamespace(file_id="x", file_size=10)]
    await save_media(public, session, alice, "WhisperBot")

    class Event:
        exception = RuntimeError("boom")
        update = SimpleNamespace(message=FakeMessage(1))

    await on_error(Event())


async def test_group_management(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    bot = FakeBot()
    created_msg = FakeMessage(1, text="/cgn Семья")
    await cmd_cgn(created_msg, command("Семья"), session, alice, "WhisperBot", FakeState())
    assert any("создана" in item.lower() for item in created_msg.answers)
    groups = await repo.list_user_groups(session, alice.id)
    group_id = groups[0][0].id
    view = FakeCallback(f"grp:view:{group_id}", message=FakeMessage(1))
    await on_group(view, session, alice, bot, "WhisperBot", FakeState())
    assert any("Семья" in item for item in view.message.answers)
    rename_cb = FakeCallback(f"grp:rename:{group_id}", message=FakeMessage(1))
    state = FakeState()
    await on_group(rename_cb, session, alice, bot, "WhisperBot", state)
    await rename_text(FakeMessage(1, text="Дом"), state, session, alice, "WhisperBot")
    group = await repo.get_group(session, group_id)
    assert group.name == "Дом"
    add_cb = FakeCallback(f"grp:add:{group_id}", message=FakeMessage(1))
    add_state = FakeState()
    await on_group(add_cb, session, alice, bot, "WhisperBot", add_state)
    await add_text(FakeMessage(1, text="2"), add_state, session, alice, bot, "WhisperBot")
    assert await repo.is_member(session, group_id, bob.id)
    await on_group(FakeCallback(f"grp:rmlist:{group_id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    await on_group(FakeCallback(f"grp:rm:{group_id}:{bob.id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    await on_group(FakeCallback(f"grp:rmok:{group_id}:{bob.id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    assert not await repo.is_member(session, group_id, bob.id)
    st = FakeState()
    await st.update_data(group_id=group_id)
    await add_text(FakeMessage(1, text="@freshuser"), st, session, alice, bot, "WhisperBot")
    await on_group(FakeCallback(f"grp:heir:{group_id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    # no other members yet if invite only
    carol = await make_user(session, 6, "carol")
    st2 = FakeState()
    await st2.update_data(group_id=group_id)
    await add_text(FakeMessage(1, text="6"), st2, session, alice, bot, "WhisperBot")
    await on_group(FakeCallback(f"grp:heir:{group_id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    await on_group(FakeCallback(f"grp:appoint:{group_id}:{carol.id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    group = await repo.get_group(session, group_id)
    assert group.successor_id == carol.id
    await on_group(FakeCallback(f"grp:hist:{group_id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="group secret",
            group_id=group_id,
            group_name=group.name,
            is_group=True,
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    hist = FakeCallback(f"grp:hist:{group_id}", message=FakeMessage(1))
    await on_group(hist, session, alice, bot, "WhisperBot", FakeState())
    assert hist.message.answers
    await on_group(FakeCallback(f"grp:leave:{group_id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    await on_group(FakeCallback(f"grp:leaveok:{group_id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState())
    assert not await repo.is_member(session, group_id, alice.id)
    listed = FakeMessage(1)
    await cmd_cgn(listed, command(None), session, carol, "WhisperBot", FakeState())
    assert listed.answers
    _ = whisper


async def test_send_flow(session, cipher):
    alice = await make_user(session, 1, "alice", default_mode="open", default_ttl=None)
    bob = await make_user(session, 2, "bob")
    bot = FakeBot(fail_ids={2})
    state = FakeState()
    await cmd_send(FakeMessage(1), state, session, alice)
    await text_recipient(FakeMessage(1, text="@bob"), state, session, alice)
    await content_text(FakeMessage(1, text="секрет через бота"), state, alice)
    await pick_mode(FakeCallback("send:mode:anonymous", message=FakeMessage(1)), state, alice)
    await pick_ttl(FakeCallback("send:ttl:60", message=FakeMessage(1)), state, alice)
    await sched_now(FakeCallback("send:sched:now", message=FakeMessage(1)), state, alice)
    done = FakeCallback("send:ok", message=FakeMessage(1))
    await send_ok(done, state, session, alice, cipher, bot, "WhisperBot")
    assert done.message.answers
    assert any("ссылк" in item.lower() or "отправлен" in item.lower() for item in done.message.answers)

    state = FakeState()
    await cmd_send(FakeMessage(1), state, session, alice)
    await text_recipient(FakeMessage(1, text="несуществующаягруппа"), state, session, alice)
    assert state.state is not None
    await pick_recipient(FakeCallback("sendto:u:bob", message=FakeMessage(1)), state, session, alice)
    photo = FakeMessage(1)
    photo.photo = [SimpleNamespace(file_id="p", file_size=20)]
    photo.caption = "caption"
    await content_media(photo, state, alice)
    await pick_mode(FakeCallback("send:mode:open", message=FakeMessage(1)), state, alice)
    custom = FakeCallback("send:ttl:custom", message=FakeMessage(1))
    await pick_ttl(custom, state, alice)
    await ttl_text(FakeMessage(1, text="1h"), state, alice)
    await sched_text(FakeMessage(1, text="2026-12-01 10:00"), state, alice)
    await send_ok(FakeCallback("send:ok", message=FakeMessage(1)), state, session, alice, cipher, bot, "WhisperBot")

    state = FakeState()
    await cmd_send(FakeMessage(1), state, session, alice)
    await repo.save_pending_media(session, alice.id, "document", "DOC", 10, utcnow())
    await use_saved_media(FakeCallback("send:media", message=FakeMessage(1)), state, session, alice)
    await skip_text(FakeCallback("send:notext", message=FakeMessage(1)), state, alice)
    await send_cancel(FakeCallback("send:no", message=FakeMessage(1)), state, alice)

    public = FakeMessage(1, chat_type="group")
    await cmd_send(public, FakeState(), session, alice)
    assert any("личке" in item for item in public.answers)
    bad_sched = FakeState()
    await bad_sched.set_state("schedule")
    await bad_sched.update_data(ttl_custom=False)
    # schedule parser error
    from bot.states import SendFlow

    await bad_sched.set_state(SendFlow.schedule)
    msg = FakeMessage(1, text="???")
    await sched_text(msg, bad_sched, alice)
    assert msg.answers
