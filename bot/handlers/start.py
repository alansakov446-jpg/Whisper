from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import User
from bot.handlers.common import access_flags, is_private
from bot.keyboards.builders import markup
from bot.services.crypto import Cipher
from bot.services.deeplink import parse_start_payload
from bot.services.delivery import send_opened_content
from bot.services.i18n import instruction, t
from bot.services.views import main_menu, ttl_choice_keyboard
from bot.services.whisper_service import destroy_whisper, plan_open, record_open
from bot.utils.time import utcnow

router = Router(name="start")


async def open_whisper_chat(message: Message, session: AsyncSession, db_user: User, cipher: Cipher, bot, token: str) -> bool:
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await message.answer(t(db_user.lang, "deep_not_found"))
        return False
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
    )
    if plan.should_destroy:
        destroy_whisper(whisper, utcnow())
    if plan.status != "ok":
        await message.answer(plan.message, protect_content=True)
        return False
    await record_open(session, whisper, db_user.id, db_user.username, utcnow())
    await send_opened_content(bot, message.chat.id, plan, whisper.token, db_user.lang)
    return True


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
    state: FSMContext,
) -> None:
    await state.clear()
    just_started = db_user.started_at is None
    if just_started:
        db_user.started_at = utcnow()
    payload = parse_start_payload(command.args if command else None)
    lang = db_user.lang
    if payload.kind == "whisper" and payload.token:
        await open_whisper_chat(message, session, db_user, cipher, bot, payload.token)
        if just_started:
            await message.answer(instruction(lang, bot_username), reply_markup=markup(main_menu(lang)))
        return
    if payload.kind == "ttl" and payload.token:
        await show_ttl_editor(message, session, db_user, cipher, payload.token)
        return
    if payload.kind == "group" and payload.group_id is not None:
        await show_group_link(message, session, db_user, bot_username, payload.group_id)
        return
    if payload.kind == "unknown":
        await message.answer(t(lang, "unknown_link"))
    await message.answer(instruction(lang, bot_username), reply_markup=markup(main_menu(lang)))


async def show_ttl_editor(message: Message, session: AsyncSession, db_user: User, cipher: Cipher, token: str) -> None:
    from bot.services.access import is_sender

    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        await message.answer(t(db_user.lang, "deep_not_found"))
        return
    if not is_sender(whisper, db_user.id, cipher):
        await message.answer(t(db_user.lang, "ttl_only_sender"))
        return
    if whisper.status == "destroyed":
        await message.answer(t(db_user.lang, "ttl_already_gone"))
        return
    await message.answer(
        t(db_user.lang, "ttl_pick"),
        reply_markup=markup(ttl_choice_keyboard(db_user.lang, token, whisper.ttl_seconds)),
    )


async def show_group_link(message: Message, session: AsyncSession, db_user: User, bot_username: str, group_id: int) -> None:
    from bot.handlers.groups import send_group_card

    group = await repo.get_group(session, group_id)
    if group is None:
        await message.answer(t(db_user.lang, "group_not_found"))
        return
    if not await repo.is_member(session, group_id, db_user.id):
        await message.answer(t(db_user.lang, "no_access_group"))
        return
    await send_group_card(message, session, db_user, bot_username, group)


@router.message(Command("help"))
async def cmd_help(message: Message, db_user: User, bot_username: str, state: FSMContext) -> None:
    await state.clear()
    await message.answer(instruction(db_user.lang, bot_username), reply_markup=markup(main_menu(db_user.lang)))


@router.message(Command("menu"))
async def cmd_menu(message: Message, db_user: User, state: FSMContext) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    await message.answer(t(db_user.lang, "menu_help"), reply_markup=markup(main_menu(db_user.lang)))


@router.callback_query(F.data == "menu:main")
async def menu_main(callback, db_user: User) -> None:
    await edit_menu(callback, instruction_line(db_user), markup(main_menu(db_user.lang)))


@router.callback_query(F.data == "menu:help")
async def menu_help(callback, db_user: User, bot_username: str) -> None:
    from bot.handlers.common import edit_or_send

    await edit_or_send(callback, instruction(db_user.lang, bot_username), markup(main_menu(db_user.lang)))


def instruction_line(db_user: User) -> str:
    return t(db_user.lang, "menu_help")


async def edit_menu(callback, text, reply_markup) -> None:
    from bot.handlers.common import edit_or_send

    await edit_or_send(callback, text, reply_markup)
