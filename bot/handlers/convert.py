from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import MAX_ALBUM_FILES
from bot.db import repo
from bot.db.models import User
from bot.handlers.common import is_private
from bot.keyboards.builders import markup
from bot.services import convert
from bot.services.i18n import t
from bot.services.media_info import extract_media, media_too_big
from bot.services.views import ButtonSpec
from bot.states import ConvertFlow
from bot.utils.time import utcnow

router = Router(name="convert")

# In-process guard: media_group ref -> key, so concurrent album updates in one
# poll batch never create two keys for the same album.
_album_seen: dict[str, str] = {}


@router.message(Command("convert"))
async def cmd_convert(message: Message, state: FSMContext, db_user: User) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "convert_private_only"))
        return
    await state.set_state(ConvertFlow.waiting)
    await message.answer(t(db_user.lang, "convert_ask"))


@router.message(Command("myconvert"))
async def cmd_myconvert(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "convert_private_only"))
        return
    rows_data = await repo.list_media_keys(session, db_user.id, limit=20)
    now = utcnow()
    alive = [row for row in rows_data if convert.key_alive(row, now)]
    if not alive:
        await message.answer(t(db_user.lang, "convert_list_empty"))
        return
    lines = [t(db_user.lang, "convert_list_title"), ""]
    buttons: list[list[ButtonSpec]] = []
    for row in alive:
        files = convert.loads_files(row.files)
        lines.append(t(db_user.lang, "convert_item", code=row.key, n=len(files)))
        buttons.append([ButtonSpec(t(db_user.lang, "delete"), callback_data=f"conv:del:{row.key}")])
    await message.answer("\n".join(lines), reply_markup=markup(buttons))


@router.callback_query(F.data.startswith("conv:del:"))
async def cb_convert_del(callback: CallbackQuery, session: AsyncSession, db_user: User) -> None:
    key = callback.data.split(":")[-1]
    deleted = await repo.delete_media_key(session, db_user.id, key)
    await callback.answer(
        t(db_user.lang, "convert_deleted") if deleted else t(db_user.lang, "convert_not_yours"),
        show_alert=not deleted,
    )


async def _send_done(message: Message, db_user: User, key: str) -> None:
    text = t(db_user.lang, "convert_done", code=key)
    try:
        await message.answer(text, parse_mode="HTML")
    except Exception:
        await message.answer(text)


@router.message(ConvertFlow.waiting, F.media_group_id)
async def on_album_item(
    message: Message,
    session: AsyncSession,
    db_user: User,
    state: FSMContext,
    session_factory=None,
) -> None:
    extracted = extract_media(message)
    if extracted is None:
        return
    kind, file_id, size = extracted
    if media_too_big(size):
        await message.answer(t(db_user.lang, "media_too_big"))
        return
    chat_id = message.chat.id if message.chat else 0
    group_ref = convert.media_group_ref(chat_id, str(message.media_group_id))
    assert group_ref is not None
    existing = None
    if group_ref in _album_seen:
        existing = await repo.get_media_key(session, _album_seen[group_ref])
    if existing is None:
        existing = await repo.get_media_key_by_group(session, group_ref)
    if existing is not None:
        _album_seen[group_ref] = existing.key
        if len(convert.loads_files(existing.files)) < MAX_ALBUM_FILES:
            await convert.append_files(session, existing, [{"type": kind, "file_id": file_id}])
        return
    result = await convert.create_key(
        session,
        owner_id=db_user.id,
        files=[{"type": kind, "file_id": file_id}],
        media_group=group_ref,
        now=utcnow(),
    )
    if result.status == "rate_limited":
        await message.answer(t(db_user.lang, "convert_rate_limit"))
        await state.clear()
        return
    if result.status == "ok":
        _album_seen[group_ref] = result.key
        await _send_done(message, db_user, result.key)
    # The key row is committed with this request session; later album items
    # append their files in their own request sessions. No background task
    # ever touches the request session.


@router.message(ConvertFlow.waiting, F.photo | F.video | F.document | F.animation | F.voice | F.audio | F.video_note)
async def on_convert_media(message: Message, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    extracted = extract_media(message)
    if extracted is None:
        await message.answer(t(db_user.lang, "send_need_content"))
        return
    kind, file_id, size = extracted
    if media_too_big(size):
        await message.answer(t(db_user.lang, "media_too_big"))
        return
    result = await convert.create_key(
        session,
        owner_id=db_user.id,
        files=[{"type": kind, "file_id": file_id}],
        now=utcnow(),
    )
    if result.status == "rate_limited":
        await message.answer(t(db_user.lang, "convert_rate_limit"))
        await state.clear()
        return
    await state.clear()
    await _send_done(message, db_user, result.key)
