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
from bot.services.delivery import send_opened_content
from bot.services.i18n import format_ttl, t
from bot.services.ttl import DurationError, parse_duration, validate_ttl
from bot.services.views import pager_buttons
from bot.services.whisper_service import (
    activate_draft,
    change_ttl,
    destroy_whisper,
    plan_open,
    record_open,
)
from bot.states import TtlFlow
from bot.utils.time import utcnow

router = Router(name="whispers")


async def _load_open(session, cipher, db_user, token: str, bot, page: int = 0, prefer_alert: bool = True):
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
) -> None:
    token = callback.data.split(":", 1)[1]
    whisper, plan = await _load_open(session, cipher, db_user, token, bot, prefer_alert=True)
    if whisper is None:
        await safe_alert(callback, t(db_user.lang, "not_found"))
        return
    if plan == "limited":
        await safe_alert(callback, t(db_user.lang, "rate_limit"))
        return
    if plan.status == "ok":
        await record_open(session, whisper, db_user.id, db_user.username, utcnow())
    await answer_open_callback(callback, bot, plan, whisper.token, db_user.lang, bot_username)


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
