from __future__ import annotations

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import TTL_PRESETS
from bot.db import repo
from bot.db.models import User
from bot.handlers.common import access_flags, notify_added, safe_alert
from bot.handlers.present import answer_open_callback, refresh_envelopes
from bot.keyboards.builders import markup
from bot.services.access import is_sender
from bot.services.crypto import Cipher
from bot.services.delivery import deliver_outgoing, send_opened_content
from bot.services.i18n import format_ttl, t
from bot.services.ttl import DurationError, parse_duration, validate_ttl
from bot.services.views import open_actions_keyboard, pager_buttons, sender_actions_keyboard
from bot.services.whisper_service import (
    activate_draft,
    change_ttl,
    delete_by_sender,
    destroy_whisper,
    extend_ttl,
    plan_open,
    record_open,
)
from bot.states import ReplyFlow, SealFlow, TtlFlow
from bot.utils.time import utcnow

router = Router(name="whispers")


async def _load_open(session, cipher, db_user, token: str, bot, page: int = 0, prefer_alert: bool = True, seal_key: str | None = None):
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        return None, None
    if whisper.status == "draft":
        result = await activate_draft(session, token, None, cipher, utcnow())
        whisper = result.whisper
        if result.new_member_ids and result.group_name:
            await notify_added(bot, session, result.new_member_ids, result.group_name)
        if result.status == "limited":
            return whisper, "limited"
    if whisper is None:
        return None, None
    member, invited = await access_flags(session, whisper, db_user.id, db_user.username)
    plan = plan_open(
        whisper,
        db_user.id,
        db_user.username,
        is_member=member,
        is_invited=invited,
        cipher=cipher,
        now=utcnow(),
        lang=db_user.lang,
        page=page,
        prefer_alert=prefer_alert,
        seal_key=seal_key,
    )
    if plan.should_destroy:
        destroy_whisper(whisper, utcnow())
    return whisper, plan


@router.callback_query(F.data.startswith("open:"))
async def cb_open(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
    state=None,
) -> None:
    token = callback.data.split(":", 1)[1]
    whisper, plan = await _load_open(session, cipher, db_user, token, bot, prefer_alert=True)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    if plan == "limited":
        await safe_alert(callback, t(db_user.lang, "rate_limit"))
        return
    if plan.status == "sealed":
        if state is not None:
            await state.set_state(SealFlow.key)
            await state.update_data(token=token)
        await safe_alert(callback, t(db_user.lang, "sealed_ask_key"))
        try:
            await bot.send_message(db_user.id, t(db_user.lang, "sealed_ask_key"))
        except Exception:
            pass
        return
    if plan.status == "sealed_bad_key":
        await safe_alert(callback, t(db_user.lang, "sealed_bad_key"))
        return
    if plan.status == "ok":
        await record_open(session, whisper, db_user.id, db_user.username, utcnow())
        from bot.services import convert as convert_service

        files = await convert_service.key_files(session, whisper.media_key, utcnow())
        plan.media_files = [(item.get("type") or "document", item.get("file_id")) for item in files if item.get("file_id")]
        pinned = await repo.is_pinned(session, db_user.id, whisper.id)
        extra_rows = open_actions_keyboard(db_user.lang, whisper.token, pinned=pinned)
    else:
        extra_rows = None
    await answer_open_callback(callback, bot, plan, whisper.token, db_user.lang, bot_username, extra_rows=extra_rows)


@router.callback_query(F.data.startswith("pg:"))
async def cb_page(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
) -> None:
    _prefix, token, raw_page = callback.data.split(":")
    whisper, plan = await _load_open(
        session, cipher, db_user, token, bot, page=int(raw_page), prefer_alert=False
    )
    if whisper is None or plan == "limited" or plan.status != "ok":
        message = t(db_user.lang, "not_found") if whisper is None else (
            t(db_user.lang, "rate_limit") if plan == "limited" else plan.message
        )
        await safe_alert(callback, message)
        return
    keyboard = markup(pager_buttons(token, plan.page, plan.pages))
    if callback.message is not None:
        await callback.message.edit_text(plan.page_text, reply_markup=keyboard)
        await callback.answer()
        return
    await send_opened_content(bot, callback.from_user.id, plan, token, db_user.lang)
    await callback.answer()


@router.callback_query(F.data.startswith("ttlset:"))
async def cb_ttlset(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
    state: FSMContext,
) -> None:
    _prefix, token, code = callback.data.split(":", 2)
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    if not is_sender(whisper, db_user.id, cipher):
        await safe_alert(callback, t(db_user.lang, "ttl_only_sender"))
        return
    if code == "custom":
        await state.set_state(TtlFlow.custom)
        await state.update_data(token=token)
        if callback.message is not None:
            await callback.message.answer(t(db_user.lang, "ttl_ask_custom"))
        await callback.answer()
        return
    if code not in TTL_PRESETS:
        await safe_alert(callback, t(db_user.lang, "ttl_bad"))
        return
    status = await change_ttl(session, whisper, db_user.id, TTL_PRESETS[code], cipher, utcnow())
    if status == "destroyed":
        await safe_alert(callback, t(db_user.lang, "ttl_already_gone"))
        return
    if status == "denied":
        await safe_alert(callback, t(db_user.lang, "ttl_only_sender"))
        return
    if status == "destroyed_now":
        from bot.services.scheduler import edit_destroyed_messages

        await edit_destroyed_messages(bot, whisper)
        await safe_alert(callback, t(db_user.lang, "destroy_soon"))
        return
    await refresh_envelopes(bot, session, whisper, bot_username)
    await safe_alert(callback, t(db_user.lang, "ttl_changed", ttl=format_ttl(whisper.ttl_seconds, db_user.lang)))


@router.message(TtlFlow.custom, F.text, ~F.text.startswith("/"))
async def ttl_custom_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
) -> None:
    data = await state.get_data()
    token = data.get("token")
    try:
        seconds = validate_ttl(parse_duration(message.text, allow_forever=True))
    except DurationError:
        await message.answer(t(db_user.lang, "ttl_bad"))
        return
    whisper = await repo.get_whisper_by_token(session, token) if token else None
    if whisper is None:
        await state.clear()
        await message.answer(t(db_user.lang, "not_found"))
        return
    status = await change_ttl(session, whisper, db_user.id, seconds, cipher, utcnow())
    await state.clear()
    if status == "destroyed_now":
        from bot.services.scheduler import edit_destroyed_messages

        await edit_destroyed_messages(bot, whisper)
        await message.answer(t(db_user.lang, "destroy_soon"))
        return
    if status != "ok":
        await message.answer(t(db_user.lang, "ttl_only_sender" if status == "denied" else "ttl_already_gone"))
        return
    await refresh_envelopes(bot, session, whisper, bot_username)
    await message.answer(t(db_user.lang, "ttl_changed", ttl=format_ttl(seconds, db_user.lang)))


@router.message(SealFlow.key, F.text, ~F.text.startswith("/"))
async def seal_key_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
) -> None:
    data = await state.get_data()
    token = data.get("token")
    whisper = await repo.get_whisper_by_token(session, token) if token else None
    if whisper is None:
        await state.clear()
        await message.answer(t(db_user.lang, "not_found"))
        return
    member, invited = await access_flags(session, whisper, db_user.id, db_user.username)
    plan = plan_open(
        whisper,
        db_user.id,
        db_user.username,
        is_member=member,
        is_invited=invited,
        cipher=cipher,
        now=utcnow(),
        lang=db_user.lang,
        prefer_alert=False,
        seal_key=(message.text or "").strip(),
    )
    if plan.status in {"sealed_bad_key", "sealed"}:
        await message.answer(t(db_user.lang, "sealed_bad_key"))
        return
    await state.clear()
    if plan.status != "ok":
        await message.answer(plan.message, protect_content=True)
        return
    await record_open(session, whisper, db_user.id, db_user.username, utcnow())
    from bot.services import convert as convert_service

    files = await convert_service.key_files(session, whisper.media_key, utcnow())
    plan.media_files = [(item.get("type") or "document", item.get("file_id")) for item in files if item.get("file_id")]
    await send_opened_content(bot, message.chat.id, plan, whisper.token, db_user.lang)


@router.callback_query(F.data.startswith("reply:"))
async def cb_reply(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    state=None,
) -> None:
    token = callback.data.split(":", 1)[1]
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    member, invited = await access_flags(session, whisper, db_user.id, db_user.username)
    now = utcnow()
    plan = plan_open(
        whisper,
        db_user.id,
        db_user.username,
        is_member=member,
        is_invited=invited,
        cipher=cipher,
        now=now,
        lang=db_user.lang,
        prefer_alert=False,
    )
    if plan.status != "ok":
        await safe_alert(callback, plan.message)
        return
    if state is not None:
        await state.set_state(ReplyFlow.text)
        await state.update_data(token=token)
    await safe_alert(callback, t(db_user.lang, "reply_ask"), show_alert=False)
    if callback.message is not None:
        await callback.message.answer(t(db_user.lang, "reply_ask"))


@router.message(ReplyFlow.text, F.text, ~F.text.startswith("/"))
async def reply_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
) -> None:
    from bot.services.crypto import resolve_sender_id
    from bot.services.whisper_service import NewWhisper, create_whisper
    from bot.utils.text import clean_secret

    data = await state.get_data()
    await state.clear()
    token = data.get("token")
    original = await repo.get_whisper_by_token(session, token) if token else None
    text = clean_secret(message.text)
    if original is None or text is None:
        await message.answer(t(db_user.lang, "reply_failed"))
        return
    member, invited = await access_flags(session, original, db_user.id, db_user.username)
    plan = plan_open(
        original,
        db_user.id,
        db_user.username,
        is_member=member,
        is_invited=invited,
        cipher=cipher,
        now=utcnow(),
        lang=db_user.lang,
        prefer_alert=False,
    )
    if plan.status != "ok":
        await message.answer(t(db_user.lang, "reply_failed"))
        return
    is_group_reply = bool(original.is_group and original.group_id)
    target_id = None
    if not is_group_reply:
        sender_id = resolve_sender_id(original, cipher)
        if sender_id is not None and sender_id != db_user.id:
            target_id = sender_id
        elif original.recipient_id and original.recipient_id != db_user.id:
            target_id = original.recipient_id
        if target_id is None:
            await message.answer(t(db_user.lang, "reply_failed"))
            return
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=db_user.id,
            sender_username=db_user.username,
            pseudonym=None,
            lang=db_user.lang,
            text=text,
            recipient_id=None if is_group_reply else target_id,
            group_id=original.group_id if is_group_reply else None,
            group_name=original.group_name if is_group_reply else None,
            is_group=is_group_reply,
            reply_to_id=original.id,
            ttl_seconds=db_user.default_ttl,
            ttl_explicit=False,
            default_ttl=db_user.default_ttl,
            delivery_mode="open",
            status="active",
        ),
    )
    report = await deliver_outgoing(bot, session, whisper, bot_username)
    if report.sent <= 0 and not is_group_reply:
        await message.answer(t(db_user.lang, "send_link", link=report.link))
        return
    await message.answer(t(db_user.lang, "reply_sent"))


@router.callback_query(F.data.startswith("pin:"))
async def cb_pin(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    token = callback.data.split(":", 1)[1]
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    from bot.db import repo as repo_module

    await repo_module.add_pin(session, db_user.id, whisper.id, utcnow())
    await safe_alert(callback, t(db_user.lang, "pinned_added"), show_alert=False)


@router.callback_query(F.data.startswith("unpin:"))
async def cb_unpin(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    token = callback.data.split(":", 1)[1]
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    from bot.db import repo as repo_module

    await repo_module.remove_pin(session, db_user.id, whisper.id)
    await safe_alert(callback, t(db_user.lang, "pinned_removed"), show_alert=False)


@router.callback_query(F.data.startswith("web:"))
async def cb_web(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    web_base_url: str = "",
) -> None:
    from bot.services.deeplink import make_web_link

    token = callback.data.split(":", 1)[1]
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    if not web_base_url:
        await safe_alert(callback, t(db_user.lang, "web_disabled"))
        return
    link = make_web_link(web_base_url, token, cipher.secret_key, utcnow())
    await safe_alert(callback, t(db_user.lang, "web_link_ready", link=link), show_alert=False)
    if callback.message is not None:
        await callback.message.answer(t(db_user.lang, "web_link_ready", link=link))


@router.callback_query(F.data.startswith("wact:"))
async def cb_wact(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
) -> None:
    parts = (callback.data or "").split(":")
    token = parts[1] if len(parts) > 1 else ""
    action = parts[2] if len(parts) > 2 else ""
    extra = parts[3] if len(parts) > 3 else ""
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    if action == "del":
        status = await delete_by_sender(session, whisper, db_user.id, cipher, utcnow())
        if status != "ok":
            await safe_alert(
                callback,
                t(db_user.lang, "delete_only_sender" if status == "denied" else "ttl_already_gone"),
            )
            return
        from bot.services.scheduler import edit_destroyed_messages

        await edit_destroyed_messages(bot, whisper, text=t(db_user.lang, "deleted_sender"))
        await safe_alert(callback, t(db_user.lang, "deleted_sender"))
        return
    if action == "ext" and extra.isdigit():
        status = await extend_ttl(session, whisper, db_user.id, int(extra), cipher, utcnow())
        if status == "forever":
            await safe_alert(callback, t(db_user.lang, "forever_no_extend"))
            return
        if status != "ok":
            await safe_alert(
                callback,
                t(db_user.lang, "delete_only_sender" if status == "denied" else "ttl_already_gone"),
            )
            return
        await refresh_envelopes(bot, session, whisper, bot_username)
        await safe_alert(callback, t(db_user.lang, "extended", ttl=format_ttl(whisper.ttl_seconds, db_user.lang)))
        return
    await safe_alert(callback, t(db_user.lang, "ttl_bad"))
