from __future__ import annotations

import json

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import BufferedInputFile, CallbackQuery, ErrorEvent, Message
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import User
from bot.handlers.common import edit_or_send, is_private
from bot.keyboards.builders import markup
from bot.services.crypto import Cipher
from bot.services.export_stats import export_user, user_stats
from bot.services.i18n import media_kind, t
from bot.services.media_info import extract_media, media_too_big
from bot.services.views import ButtonSpec, clip_button, display_user, main_menu
from bot.utils.text import clip
from bot.utils.time import format_human, utcnow

router = Router(name="misc")


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    await message.answer(t(db_user.lang, "cancelled"), reply_markup=markup(main_menu(db_user.lang)))


@router.callback_query(F.data == "noop")
async def noop(callback: CallbackQuery) -> None:
    await callback.answer()


@router.message(Command("stats"))
async def cmd_stats(message: Message, session: AsyncSession, db_user: User, cipher: Cipher, state: FSMContext) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    stats = await user_stats(session, db_user.id, cipher)
    await message.answer(t(db_user.lang, "stats", **stats))


@router.message(Command("export"))
async def cmd_export(message: Message, session: AsyncSession, db_user: User, cipher: Cipher, state: FSMContext) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    payload = await export_user(session, db_user.id, cipher, utcnow())
    if not payload["sent"] and not payload["received"]:
        await message.answer(t(db_user.lang, "export_empty"))
        return
    raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    await message.answer_document(
        BufferedInputFile(raw, filename="whispers.json"),
        caption=t(db_user.lang, "export_caption"),
        protect_content=True,
    )


@router.message(Command("nomedia"))
async def cmd_nomedia(message: Message, session: AsyncSession, db_user: User) -> None:
    media = await repo.get_pending_media(session, db_user.id)
    if media is None:
        await message.answer(t(db_user.lang, "media_none"))
        return
    await repo.clear_pending_media(session, db_user.id)
    await message.answer(t(db_user.lang, "media_cleared"))


@router.message(Command("search"))
async def cmd_search(
    message: Message,
    command,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    state: FSMContext,
) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    args = (getattr(command, "args", None) or "").strip()
    if not args:
        await message.answer(t(db_user.lang, "search_usage"))
        return
    from bot.utils.text import preview

    results = await repo.search_whispers(session, db_user.id, cipher.hmac_sender(db_user.id), args, limit=10)
    if not results:
        await message.answer(t(db_user.lang, "search_empty", query=clip(args, 32)))
        return
    rows = []
    for whisper in results:
        label = t(
            db_user.lang,
            "search_hit",
            when=format_human(whisper.created_at),
            preview=preview(whisper.text or whisper.media_type or whisper.status, 24),
        )
        rows.append([ButtonSpec(clip_button(label, 60), callback_data=f"open:{whisper.token}")])
    await message.answer(t(db_user.lang, "search_title"), reply_markup=markup(rows))


@router.message(Command("pins"))
async def cmd_pins(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    from bot.utils.text import preview

    pins = await repo.list_pins(session, db_user.id, limit=20)
    if not pins:
        await message.answer(t(db_user.lang, "pins_empty"))
        return
    rows = []
    for _pin, whisper in pins:
        label = t(
            db_user.lang,
            "search_hit",
            when=format_human(whisper.created_at),
            preview=preview(whisper.text or whisper.media_type or whisper.status, 24),
        )
        rows.append(
            [
                ButtonSpec(clip_button(label, 48), callback_data=f"open:{whisper.token}"),
                ButtonSpec(t(db_user.lang, "delete"), callback_data=f"unpin:{whisper.token}"),
            ]
        )
    await message.answer(t(db_user.lang, "pins_title"), reply_markup=markup(rows))


@router.callback_query(F.data == "menu:contacts")
async def menu_contacts(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    contacts = await repo.list_contacts(session, db_user.id, limit=20)
    lang = db_user.lang
    if not contacts:
        await edit_or_send(
            callback,
            t(lang, "contacts_title") + "\n\n" + t(lang, "contacts_empty"),
            markup([[ButtonSpec(t(lang, "back"), callback_data="menu:main")]]),
        )
        return
    rows = []
    for contact in contacts:
        label = (
            ("⭐ " if contact.favorite else "")
            + display_user(
                contact.username,
                contact.contact_user_id,
                lang,
                contact.first_name,
                contact.last_name,
            )
        )
        row = [
            ButtonSpec(
                label,
                callback_data=f"sendto:u:{contact.username}" if contact.username else f"sendto:id:{contact.contact_user_id}",
            )
        ]
        if not contact.username and contact.contact_user_id:
            row.append(ButtonSpec("🔗", url=f"tg://user?id={contact.contact_user_id}"))
        row.append(ButtonSpec("⭐" if not contact.favorite else "☆", callback_data=f"contact:fav:{contact.id}"))
        row.append(ButtonSpec(t(lang, "delete"), callback_data=f"contact:del:{contact.id}"))
        rows.append(row)
    rows.append([ButtonSpec(t(lang, "back"), callback_data="menu:main")])
    await edit_or_send(callback, t(lang, "contacts_title"), markup(rows))


@router.callback_query(F.data.startswith("contact:fav:"))
async def favorite_contact_cb(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    contact_id = int(callback.data.split(":")[-1])
    contact = await repo.get_contact(session, db_user.id, contact_id)
    if contact is None:
        await callback.answer(t(db_user.lang, "not_found"), show_alert=True)
        return
    await repo.set_contact_favorite(session, db_user.id, contact_id, not contact.favorite)
    await callback.answer()
    await menu_contacts(callback, session, db_user)


@router.callback_query(F.data.startswith("contact:del:"))
async def delete_contact_cb(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    contact_id = int(callback.data.split(":")[-1])
    deleted = await repo.delete_contact(session, db_user.id, contact_id)
    await callback.answer(t(db_user.lang, "contact_deleted") if deleted else t(db_user.lang, "not_found"), show_alert=not deleted)
    await menu_contacts(callback, session, db_user)


@router.message(StateFilter(None), F.photo | F.video | F.document | F.animation | F.voice | F.audio | F.video_note)
async def save_media(message: Message, session: AsyncSession, db_user: User, bot_username: str) -> None:
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    extracted = extract_media(message)
    if extracted is None:
        return
    kind, file_id, size = extracted
    if media_too_big(size):
        await message.answer(t(db_user.lang, "media_too_big"))
        return
    await repo.save_pending_media(session, db_user.id, kind, file_id, size, utcnow())
    await message.answer(t(db_user.lang, "media_saved", kind=media_kind(db_user.lang, kind), bot=bot_username))


async def on_error(event: ErrorEvent) -> None:
    logger.exception("handler failed: {}", event.exception)
    message = event.update.message if event.update else None
    if message is not None:
        try:
            await message.answer(t("ru", "error"))
        except Exception:
            logger.debug("could not report error to user")
