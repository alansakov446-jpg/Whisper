from __future__ import annotations

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from loguru import logger

from bot.db import repo
from bot.keyboards.builders import markup
from bot.services.deeplink import whisper_link
from bot.services.delivery import send_opened_content
from bot.services.i18n import t
from bot.services.views import envelope_buttons, envelope_text
from bot.services.whisper_service import collect_refs


async def refresh_envelopes(bot, session, whisper, bot_username: str) -> None:
    size = 0
    if whisper.group_id:
        members = await repo.list_members(session, whisper.group_id)
        size = len(members)
    text = envelope_text(
        lang=whisper.lang,
        recipient_username=whisper.recipient_username,
        recipient_id=whisper.recipient_id,
        group_name=whisper.group_name,
        group_size=size,
        is_group=whisper.is_group,
        is_anonymous=whisper.is_anonymous,
        pseudonym=whisper.pseudonym_snapshot,
        sender_username=whisper.sender_username,
        delivery_mode=whisper.delivery_mode,
        ttl_seconds=whisper.ttl_seconds,
    )
    keyboard = markup(
        envelope_buttons(
            lang=whisper.lang,
            token=whisper.token,
            bot_username=bot_username,
            has_media=bool(whisper.media_file_id),
        )
    )
    seen: set[tuple] = set()
    for ref in collect_refs(whisper):
        key = tuple(sorted(ref.items()))
        if key in seen:
            continue
        seen.add(key)
        try:
            await bot.edit_message_text(text, reply_markup=keyboard, **ref)
        except Exception as exc:
            logger.info("envelope refresh failed: {}", type(exc).__name__)


async def answer_open_callback(callback, bot, plan, token: str, lang: str, bot_username: str) -> None:
    if plan.status != "ok":
        await _alert(callback, plan.message)
        if plan.status == "destroyed" and callback.inline_message_id:
            try:
                await bot.edit_message_text(
                    plan.message,
                    inline_message_id=callback.inline_message_id,
                    reply_markup=None,
                )
            except Exception:
                logger.info("could not edit destroyed inline message")
        return
    if plan.use_alert and not plan.media_type:
        await _alert(callback, plan.message)
        return
    chat_id = callback.from_user.id
    try:
        await send_opened_content(bot, chat_id, plan, token, lang)
        await _alert(callback, t(lang, "opened_in_dm"), show_alert=False)
    except (TelegramForbiddenError, TelegramBadRequest):
        link = whisper_link(bot_username, token)
        await _alert(callback, t(lang, "activate_title") + "\n" + link)


async def _alert(callback, text: str, show_alert: bool = True) -> None:
    try:
        if show_alert:
            await callback.answer(text[:200], show_alert=True)
        else:
            await callback.answer(text[:200])
    except TelegramBadRequest:
        pass
