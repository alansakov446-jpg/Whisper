from __future__ import annotations

from datetime import datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import MAX_TEXT_LEN, TTL_PRESETS
from bot.db import repo
from bot.db.models import User
from bot.handlers.common import is_private
from bot.keyboards.builders import markup
from bot.services.crypto import Cipher
from bot.services.deeplink import ttl_link
from bot.services.delivery import DeliveryReport, deliver_outgoing, sender_result_text
from bot.services.i18n import format_ttl, media_kind, t
from bot.services.media_info import extract_media, media_too_big
from bot.services.recipients import resolve_recipient
from bot.services.ttl import DurationError, parse_duration, parse_schedule, validate_ttl
from bot.services.views import ButtonSpec, clip_button, send_mode_keyboard, send_ttl_keyboard
from bot.services.whisper_service import NewWhisper, create_whisper, is_rate_limited, remember_contact
from bot.states import SendFlow
from bot.utils.text import clean_secret, clip, preview
from bot.utils.time import format_human, utcnow

router = Router(name="send")


def _who(lang: str, data: dict) -> str:
    if data.get("recipient_type") == "group":
        return t(lang, "send_who_group", name=data.get("group_name") or "—")
    if data.get("recipient_username"):
        return t(lang, "send_who_user", username=data["recipient_username"])
    if data.get("recipient_id"):
        return t(lang, "id_label", id=data["recipient_id"])
    return "—"


async def begin_send(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    await state.set_state(SendFlow.recipient)
    await state.set_data({})
    contacts = await repo.list_contacts(session, db_user.id, limit=6)
    groups = await repo.list_user_groups(session, db_user.id)
    rows: list[list[ButtonSpec]] = []
    for contact in contacts:
        if contact.username:
            rows.append([ButtonSpec(f"@{contact.username}", callback_data=f"sendto:u:{contact.username}")])
        elif contact.contact_user_id:
            rows.append(
                [ButtonSpec(t(db_user.lang, "id_label", id=contact.contact_user_id), callback_data=f"sendto:id:{contact.contact_user_id}")]
            )
    for group, count in groups[:6]:
        rows.append(
            [
                ButtonSpec(
                    t(db_user.lang, "group_btn", name=clip_button(group.name, 28), n=count),
                    callback_data=f"sendto:g:{group.id}",
                )
            ]
        )
    await message.answer(t(db_user.lang, "send_ask_who"), reply_markup=markup(rows))


async def ask_content(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    await state.set_state(SendFlow.content)
    media = await repo.get_pending_media(session, db_user.id)
    rows = []
    if media:
        rows.append([ButtonSpec(t(db_user.lang, "send_use_media"), callback_data="send:media")])
    await message.answer(t(db_user.lang, "send_ask_content"), reply_markup=markup(rows))


async def ask_mode(message: Message, state: FSMContext, db_user: User) -> None:
    await state.set_state(SendFlow.mode)
    data = await state.get_data()
    current = data.get("mode") or db_user.default_mode or "open"
    await state.update_data(mode=current)
    await message.answer(t(db_user.lang, "send_ask_mode"), reply_markup=markup(send_mode_keyboard(db_user.lang, current)))


async def ask_ttl(message: Message, state: FSMContext, db_user: User) -> None:
    await state.set_state(SendFlow.ttl)
    await message.answer(
        t(db_user.lang, "send_ask_ttl"),
        reply_markup=markup(send_ttl_keyboard(db_user.lang, db_user.default_ttl)),
    )


async def ask_schedule(message: Message, state: FSMContext, db_user: User) -> None:
    await state.set_state(SendFlow.schedule)
    await message.answer(
        t(db_user.lang, "send_ask_schedule"),
        reply_markup=markup([[ButtonSpec(t(db_user.lang, "send_skip_schedule"), callback_data="send:sched:now")]]),
    )


async def ask_confirm(message: Message, state: FSMContext, db_user: User, send_at: datetime) -> None:
    await state.update_data(send_at=send_at.strftime("%Y-%m-%dT%H:%M:%S"))
    await state.set_state(SendFlow.confirm)
    data = await state.get_data()
    lang = db_user.lang
    mode = data.get("mode") or "open"
    ttl_seconds = data.get("ttl_seconds", db_user.default_ttl)
    when = t(lang, "when_now") if send_at <= utcnow() else format_human(send_at)
    body = data.get("text") or ""
    if data.get("media_type"):
        extra = "📎 " + media_kind(lang, data.get("media_type"))
        body = (body + "\n" + extra).strip()
    text = t(
        lang,
        "send_confirm",
        who=_who(lang, data),
        mode=t(lang, "mode_anon_label" if mode == "anonymous" else "mode_open_label"),
        ttl=format_ttl(ttl_seconds, lang),
        when=when,
        preview=preview(body, 300) or t(lang, "empty_secret"),
    )
    await message.answer(
        text,
        protect_content=True,
        reply_markup=markup(
            [
                [ButtonSpec(t(lang, "send_confirm_btn"), callback_data="send:ok")],
                [ButtonSpec(t(lang, "send_cancel_btn"), callback_data="send:no")],
            ]
        ),
    )


@router.message(Command("send"))
async def cmd_send(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    await begin_send(message, state, session, db_user)


@router.callback_query(F.data == "menu:send")
async def menu_send(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    if callback.message is None or not is_private(callback.message):
        await callback.answer(t(db_user.lang, "private_only"), show_alert=True)
        return
    await callback.answer()
    await begin_send(callback.message, state, session, db_user)


@router.callback_query(F.data.startswith("sendto:"))
async def pick_recipient(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
) -> None:
    _prefix, kind, value = callback.data.split(":", 2)
    if kind == "u":
        user = await repo.get_user_by_username(session, value)
        await state.update_data(recipient_type="user", recipient_username=value, recipient_id=user.id if user else None)
    elif kind == "id":
        user = await repo.get_user(session, int(value))
        await state.update_data(
            recipient_type="user",
            recipient_id=int(value),
            recipient_username=user.username if user else None,
        )
    elif kind == "g":
        group = await repo.get_group(session, int(value))
        if group is None or not await repo.is_member(session, group.id, db_user.id):
            await callback.answer(t(db_user.lang, "group_not_member"), show_alert=True)
            return
        await state.update_data(recipient_type="group", group_id=group.id, group_name=group.name)
    else:
        await callback.answer()
        return
    await callback.answer()
    await ask_content(callback.message, state, session, db_user)


@router.message(SendFlow.recipient, F.text, ~F.text.startswith("/"))
async def text_recipient(message: Message, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    resolved = await resolve_recipient(session, db_user.id, message.text)
    if resolved["type"] == "invalid":
        await message.answer(t(db_user.lang, "send_bad_who"))
        return
    if resolved["type"] == "group":
        await state.update_data(recipient_type="group", group_id=resolved["group_id"], group_name=resolved["group_name"])
    else:
        await state.update_data(
            recipient_type="user",
            recipient_id=resolved.get("user_id"),
            recipient_username=resolved.get("username"),
        )
    await ask_content(message, state, session, db_user)


@router.callback_query(F.data == "send:media")
async def use_saved_media(callback: CallbackQuery, state: FSMContext, session: AsyncSession, db_user: User) -> None:
    media = await repo.get_pending_media(session, db_user.id)
    if media is None:
        await callback.answer(t(db_user.lang, "media_none"), show_alert=True)
        return
    await state.update_data(media_type=media.media_type, media_file_id=media.file_id)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            t(db_user.lang, "send_ask_content"),
            reply_markup=markup([[ButtonSpec(t(db_user.lang, "send_skip_text"), callback_data="send:notext")]]),
        )


@router.callback_query(F.data == "send:notext")
async def skip_text(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if not data.get("media_file_id"):
        await callback.answer(t(db_user.lang, "send_need_content"), show_alert=True)
        return
    await state.update_data(text=None)
    await callback.answer()
    await ask_mode(callback.message, state, db_user)


@router.message(SendFlow.content, F.text, ~F.text.startswith("/"))
async def content_text(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    raw = message.text or ""
    if raw.strip() in {"-", "—"} and data.get("media_file_id"):
        await state.update_data(text=None)
        await ask_mode(message, state, db_user)
        return
    text = clean_secret(raw)
    if text is None:
        await message.answer(t(db_user.lang, "send_need_content"))
        return
    if len(text) > MAX_TEXT_LEN:
        await message.answer(t(db_user.lang, "text_too_long"))
        return
    await state.update_data(text=text)
    await ask_mode(message, state, db_user)


@router.message(SendFlow.content, F.photo | F.video | F.document | F.animation | F.voice | F.audio | F.video_note)
async def content_media(message: Message, state: FSMContext, db_user: User) -> None:
    extracted = extract_media(message)
    if extracted is None:
        await message.answer(t(db_user.lang, "send_need_content"))
        return
    kind, file_id, size = extracted
    if media_too_big(size):
        await message.answer(t(db_user.lang, "media_too_big"))
        return
    caption = clean_secret(message.caption)
    if caption and len(caption) > MAX_TEXT_LEN:
        await message.answer(t(db_user.lang, "text_too_long"))
        return
    await state.update_data(media_type=kind, media_file_id=file_id, text=caption)
    if caption:
        await ask_mode(message, state, db_user)
        return
    await message.answer(
        t(db_user.lang, "send_ask_content"),
        reply_markup=markup([[ButtonSpec(t(db_user.lang, "send_skip_text"), callback_data="send:notext")]]),
    )


@router.callback_query(F.data.startswith("send:mode:"))
async def pick_mode(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    mode = callback.data.split(":")[-1]
    if mode not in {"open", "anonymous"}:
        await callback.answer()
        return
    await state.update_data(mode=mode)
    await callback.answer()
    await ask_ttl(callback.message, state, db_user)


@router.callback_query(F.data.startswith("send:ttl:"))
async def pick_ttl(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    code = callback.data.split(":")[-1]
    if code == "custom":
        await state.set_state(SendFlow.ttl)
        await state.update_data(ttl_custom=True)
        if callback.message:
            await callback.message.answer(t(db_user.lang, "ttl_ask_custom"))
        await callback.answer()
        return
    if code not in TTL_PRESETS:
        await callback.answer(t(db_user.lang, "ttl_bad"), show_alert=True)
        return
    await state.update_data(ttl_code=code, ttl_seconds=TTL_PRESETS[code], ttl_set=True)
    await callback.answer()
    await ask_schedule(callback.message, state, db_user)


@router.message(SendFlow.ttl, F.text, ~F.text.startswith("/"))
async def ttl_text(message: Message, state: FSMContext, db_user: User) -> None:
    data = await state.get_data()
    if not data.get("ttl_custom"):
        await message.answer(t(db_user.lang, "send_ask_ttl"))
        return
    try:
        seconds = validate_ttl(parse_duration(message.text, allow_forever=True))
    except DurationError:
        await message.answer(t(db_user.lang, "ttl_bad"))
        return
    await state.update_data(ttl_code="custom", ttl_seconds=seconds, ttl_set=True, ttl_custom=False)
    await ask_schedule(message, state, db_user)


@router.callback_query(F.data == "send:sched:now")
async def sched_now(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await callback.answer()
    await ask_confirm(callback.message, state, db_user, utcnow())


@router.message(SendFlow.schedule, F.text, ~F.text.startswith("/"))
async def sched_text(message: Message, state: FSMContext, db_user: User) -> None:
    now = utcnow()
    try:
        when = parse_schedule(message.text, now)
    except DurationError:
        await message.answer(t(db_user.lang, "send_bad_schedule"))
        return
    if when < now:
        await message.answer(t(db_user.lang, "send_schedule_past"))
        return
    await ask_confirm(message, state, db_user, when)


@router.callback_query(F.data == "send:no")
async def send_cancel(callback: CallbackQuery, state: FSMContext, db_user: User) -> None:
    await state.clear()
    await callback.answer(t(db_user.lang, "send_cancelled"))
    if callback.message:
        await callback.message.answer(t(db_user.lang, "send_cancelled"))


@router.callback_query(F.data == "send:ok")
async def send_ok(
    callback: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
    bot_username: str,
) -> None:
    data = await state.get_data()
    lang = db_user.lang
    if not data.get("text") and not data.get("media_file_id"):
        await callback.answer(t(lang, "send_need_content"), show_alert=True)
        return
    if await is_rate_limited(session, cipher.hmac_sender(db_user.id), utcnow()):
        await callback.answer(t(lang, "rate_limit"), show_alert=True)
        return
    send_at = datetime.strptime(data["send_at"], "%Y-%m-%dT%H:%M:%S") if data.get("send_at") else utcnow()
    now = utcnow()
    scheduled = send_at > now
    mode = data.get("mode") or db_user.default_mode or "open"
    anonymous = mode == "anonymous"
    is_group = data.get("recipient_type") == "group"
    if data.get("ttl_set"):
        code = data.get("ttl_code")
        ttl_seconds = TTL_PRESETS[code] if code in TTL_PRESETS else data.get("ttl_seconds")
    else:
        ttl_seconds = db_user.default_ttl
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=db_user.id,
            sender_username=None if anonymous else db_user.username,
            pseudonym=None if anonymous else db_user.pseudonym,
            lang=lang,
            text=data.get("text"),
            recipient_id=None if is_group else data.get("recipient_id"),
            recipient_username=None if is_group else data.get("recipient_username"),
            group_id=data.get("group_id") if is_group else None,
            group_name=data.get("group_name") if is_group else None,
            is_group=is_group,
            media_type=data.get("media_type"),
            media_file_id=data.get("media_file_id"),
            ttl_seconds=ttl_seconds,
            ttl_explicit=True,
            default_ttl=db_user.default_ttl,
            delivery_mode="anonymous" if anonymous else "open",
            is_anonymous=anonymous,
            send_at=send_at,
            status="scheduled" if scheduled else "active",
            now=now,
        ),
    )
    if not is_group:
        await remember_contact(session, db_user.id, data.get("recipient_username"), data.get("recipient_id"), now)
    if data.get("media_file_id"):
        await repo.clear_pending_media(session, db_user.id)
    await state.clear()
    if scheduled:
        text = sender_result_text(lang, DeliveryReport(), scheduled=True, when=format_human(send_at), group=is_group)
        await callback.message.answer(text, protect_content=True)
        await callback.answer()
        return
    report = await deliver_outgoing(bot, session, whisper, bot_username)
    text = sender_result_text(lang, report, scheduled=False, when="", group=is_group)
    if anonymous:
        text += "\n\n" + t(lang, "send_anon_hint")
    rows = [
        [ButtonSpec(t(lang, "open_button"), callback_data=f"open:{whisper.token}")],
        [ButtonSpec(t(lang, "ttl_button"), url=ttl_link(bot_username, whisper.token))],
    ]
    await callback.message.answer(text, protect_content=True, reply_markup=markup(rows))
    await callback.answer(t(lang, "send_done"))
