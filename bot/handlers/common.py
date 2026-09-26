from __future__ import annotations

from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from loguru import logger

from bot.db import repo
from bot.services.i18n import t


def is_private(event) -> bool:
    chat = getattr(event, "chat", None)
    if chat is None and getattr(event, "message", None) is not None:
        chat = event.message.chat
    if chat is None:
        return False
    chat_type = getattr(chat, "type", None)
    return chat_type in {ChatType.PRIVATE, "private"}


async def access_flags(session, whisper, user_id: int, username: str | None) -> tuple[bool, bool]:
    if not whisper.group_id:
        return False, False
    member = await repo.is_member(session, whisper.group_id, user_id)
    invited = await repo.is_invited(session, whisper.group_id, username)
    return member, invited


async def notify_added(bot, session, user_ids: list[int], group_name: str) -> None:
    for user_id in user_ids:
        user = await repo.get_user(session, user_id)
        if user is None or not user.notifications or user.started_at is None:
            continue
        try:
            await bot.send_message(user.id, t(user.lang, "group_notify_added", name=group_name))
        except Exception:
            logger.info("notify failed for {}", user_id)


async def edit_or_send(callback, text: str, reply_markup=None) -> None:
    if callback.message is not None:
        try:
            await callback.message.edit_text(text, reply_markup=reply_markup)
        except TelegramBadRequest:
            await callback.message.answer(text, reply_markup=reply_markup)
    try:
        await callback.answer()
    except TelegramBadRequest:
        pass


async def safe_alert(callback, text: str, show_alert: bool = True) -> None:
    try:
        await callback.answer(text[:200], show_alert=show_alert)
    except TelegramBadRequest:
        logger.debug("callback answer failed")
