from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import TTL_PRESETS
from bot.db.models import User
from bot.handlers.common import edit_or_send, is_private
from bot.keyboards.builders import markup
from bot.services.i18n import format_ttl, t
from bot.services.ttl import DurationError, parse_duration, validate_ttl
from bot.services.views import settings_keyboard, settings_text
from bot.states import SettingsFlow
from bot.utils.text import parse_pseudonym

router = Router(name="settings")


async def show_settings(target, db_user: User, *, edit: bool = False) -> None:
    text = settings_text(db_user.lang, db_user)
    keyboard = markup(settings_keyboard(db_user.lang, db_user))
    if edit:
        await edit_or_send(target, text, keyboard)
        return
    await target.answer(text, reply_markup=keyboard)


@router.message(Command("settings"))
async def cmd_settings(message: Message, db_user: User, state: FSMContext) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    await show_settings(message, db_user)


@router.callback_query(F.data == "menu:settings")
async def menu_settings(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.clear()
    if callback.message and not is_private(callback.message):
        await callback.answer(t(db_user.lang, "private_only"), show_alert=True)
        return
    await show_settings(callback, db_user, edit=True)


@router.callback_query(F.data.startswith("set:ttl:"))
async def set_ttl(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    code = callback.data.split(":")[-1]
    if code == "custom":
        await state.set_state(SettingsFlow.custom_ttl)
        if callback.message:
            await callback.message.answer(t(db_user.lang, "ttl_ask_custom"))
        await callback.answer()
        return
    if code not in TTL_PRESETS:
        await callback.answer(t(db_user.lang, "ttl_bad"), show_alert=True)
        return
    db_user.default_ttl = TTL_PRESETS[code]
    await show_settings(callback, db_user, edit=True)


@router.message(SettingsFlow.custom_ttl, F.text, ~F.text.startswith("/"))
async def custom_default_ttl(message: Message, state: FSMContext, db_user: User) -> None:
    try:
        seconds = validate_ttl(parse_duration(message.text, allow_forever=True))
    except DurationError:
        await message.answer(t(db_user.lang, "ttl_bad"))
        return
    db_user.default_ttl = seconds
    await state.clear()
    await message.answer(t(db_user.lang, "custom_ttl_saved", ttl=format_ttl(seconds, db_user.lang)))
    await show_settings(message, db_user)


@router.callback_query(F.data.startswith("set:mode:"))
async def set_mode(callback: CallbackQuery, db_user: User) -> None:
    mode = callback.data.split(":")[-1]
    if mode not in {"open", "anonymous"}:
        await callback.answer()
        return
    db_user.default_mode = mode
    await show_settings(callback, db_user, edit=True)


@router.callback_query(F.data == "set:notif")
async def set_notif(callback: CallbackQuery, db_user: User) -> None:
    db_user.notifications = not db_user.notifications
    await show_settings(callback, db_user, edit=True)


@router.callback_query(F.data.startswith("set:lang:"))
async def set_lang(callback: CallbackQuery, db_user: User) -> None:
    lang = callback.data.split(":")[-1]
    if lang not in {"ru", "en"}:
        await callback.answer()
        return
    db_user.lang = lang
    await show_settings(callback, db_user, edit=True)


@router.callback_query(F.data == "set:pseudo")
async def ask_pseudo(callback: CallbackQuery, db_user: User, state: FSMContext) -> None:
    await state.set_state(SettingsFlow.pseudonym)
    if callback.message:
        await callback.message.answer(t(db_user.lang, "pseudo_ask"))
    await callback.answer()


@router.message(SettingsFlow.pseudonym, F.text, ~F.text.startswith("/"))
async def save_pseudo(message: Message, state: FSMContext, db_user: User) -> None:
    ok, value = parse_pseudonym(message.text)
    if not ok:
        await message.answer(t(db_user.lang, "pseudo_bad"))
        return
    db_user.pseudonym = value
    await state.clear()
    if value:
        await message.answer(t(db_user.lang, "pseudo_saved", value=value))
    else:
        await message.answer(t(db_user.lang, "pseudo_cleared"))
    await show_settings(message, db_user)
