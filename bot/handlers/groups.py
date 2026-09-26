from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import User
from bot.handlers.common import edit_or_send, is_private, notify_added
from bot.keyboards.builders import markup
from bot.services.group_service import (
    add_member,
    create_group,
    leave_group,
    remove_member,
    rename_group,
    set_successor,
    transfer_now,
)
from bot.services.i18n import t
from bot.services.views import ButtonSpec, clip_button, display_user, group_buttons, group_card_text
from bot.states import GroupFlow
from bot.utils.text import clip, parse_cgn_args
from bot.utils.time import format_human, utcnow

router = Router(name="groups")


async def send_group_card(target, session: AsyncSession, db_user: User, bot_username: str, group, *, edit: bool = False) -> None:
    members = await repo.list_members(session, group.id)
    creator = await repo.get_user(session, group.creator_id)
    creator_name = display_user(creator.username if creator else None, group.creator_id, db_user.lang)
    heir_name = None
    if group.successor_id:
        heir = next((member for member in members if member.user_id == group.successor_id), None)
        heir_user = await repo.get_user(session, group.successor_id)
        heir_name = display_user(
            (heir.username if heir and heir.username else (heir_user.username if heir_user else None)),
            group.successor_id,
            db_user.lang,
        )
    text = group_card_text(db_user.lang, group, members, creator_name, heir_name, bot_username)
    keyboard = markup(group_buttons(db_user.lang, group, db_user.id))
    if edit:
        await edit_or_send(target, text, keyboard)
        return
    await target.answer(text, reply_markup=keyboard)


async def show_group_list(target, session: AsyncSession, db_user: User, *, edit: bool = False) -> None:
    groups = await repo.list_user_groups(session, db_user.id)
    lang = db_user.lang
    if not groups:
        text = t(lang, "groups_title") + "\n\n" + t(lang, "groups_empty")
        keyboard = markup([[ButtonSpec(t(lang, "back"), callback_data="menu:main")]])
    else:
        rows = [
            [ButtonSpec(t(lang, "group_btn", name=clip_button(group.name, 40), n=count), callback_data=f"grp:view:{group.id}")]
            for group, count in groups[:30]
        ]
        rows.append([ButtonSpec(t(lang, "back"), callback_data="menu:main")])
        text = t(lang, "groups_title")
        keyboard = markup(rows)
    if edit:
        await edit_or_send(target, text, keyboard)
        return
    await target.answer(text, reply_markup=keyboard)


@router.message(Command("cgn"))
async def cmd_cgn(
    message: Message,
    command: CommandObject,
    session: AsyncSession,
    db_user: User,
    bot_username: str,
    state: FSMContext,
) -> None:
    await state.clear()
    if not is_private(message):
        await message.answer(t(db_user.lang, "private_only"))
        return
    name = parse_cgn_args(command.args if command else None)
    if not name:
        await show_group_list(message, session, db_user)
        return
    created = await create_group(
        session,
        name=name,
        creator_id=db_user.id,
        creator_username=db_user.username,
        now=utcnow(),
    )
    await message.answer(t(db_user.lang, "group_created", name=created.group.name))
    await send_group_card(message, session, db_user, bot_username, created.group)


@router.callback_query(F.data == "menu:groups")
async def menu_groups(callback: CallbackQuery, session: AsyncSession, db_user: User, state: FSMContext) -> None:
    await state.clear()
    await show_group_list(callback, session, db_user, edit=True)


@router.callback_query(F.data.startswith("grp:"))
async def on_group(
    callback: CallbackQuery,
    session: AsyncSession,
    db_user: User,
    bot,
    bot_username: str,
    state: FSMContext,
) -> None:
    parts = (callback.data or "").split(":")
    if len(parts) < 3 or not parts[2].isdigit():
        await callback.answer()
        return
    action = parts[1]
    group_id = int(parts[2])
    extra = parts[3] if len(parts) > 3 else None
    group = await repo.get_group(session, group_id)
    lang = db_user.lang
    if group is None and action not in {"leaveok"}:
        await callback.answer(t(lang, "group_not_found"), show_alert=True)
        return
    if action == "view" and group is not None:
        if not await repo.is_member(session, group_id, db_user.id):
            await callback.answer(t(lang, "group_not_member"), show_alert=True)
            return
        await send_group_card(callback, session, db_user, bot_username, group, edit=True)
        return
    if action == "rename" and group is not None:
        if group.creator_id != db_user.id:
            await callback.answer(t(lang, "group_not_creator"), show_alert=True)
            return
        await state.set_state(GroupFlow.rename)
        await state.update_data(group_id=group_id)
        if callback.message:
            await callback.message.answer(t(lang, "group_ask_name"))
        await callback.answer()
        return
    if action == "add" and group is not None:
        if group.creator_id != db_user.id:
            await callback.answer(t(lang, "group_not_creator"), show_alert=True)
            return
        await state.set_state(GroupFlow.add_member)
        await state.update_data(group_id=group_id)
        if callback.message:
            await callback.message.answer(t(lang, "group_ask_add"))
        await callback.answer()
        return
    if action == "rmlist" and group is not None:
        await _show_remove_list(callback, session, db_user, group)
        return
    if action == "rm" and extra and extra.isdigit() and group is not None:
        await callback.message.answer(
            t(lang, "group_rm_confirm"),
            reply_markup=markup(
                [
                    [ButtonSpec(t(lang, "yes"), callback_data=f"grp:rmok:{group_id}:{extra}")],
                    [ButtonSpec(t(lang, "no"), callback_data=f"grp:view:{group_id}")],
                ]
            ),
        )
        await callback.answer()
        return
    if action == "rmok" and extra and extra.isdigit():
        status = await remove_member(session, group_id, db_user.id, int(extra))
        await _after_mutation(callback, session, db_user, bot_username, group_id, status, removed=True)
        return
    if action == "leave" and group is not None:
        heir = t(lang, "group_no_heir") if group.creator_id == db_user.id and not group.successor_id else ""
        text = (
            t(lang, "group_leave_creator_confirm", heir=heir)
            if group.creator_id == db_user.id
            else t(lang, "group_leave_confirm", name=group.name)
        )
        await callback.message.answer(
            text,
            reply_markup=markup(
                [
                    [ButtonSpec(t(lang, "yes"), callback_data=f"grp:leaveok:{group_id}")],
                    [ButtonSpec(t(lang, "no"), callback_data=f"grp:view:{group_id}")],
                ]
            ),
        )
        await callback.answer()
        return
    if action == "leaveok":
        result = await leave_group(session, group_id, db_user.id)
        if not result.ok:
            await callback.answer(t(lang, "group_not_member"), show_alert=True)
            return
        if result.dissolved:
            await callback.message.answer(t(lang, "group_dissolved", name=result.group_name))
        elif result.new_creator_id:
            who = display_user(None, result.new_creator_id, lang)
            creator = await repo.get_user(session, result.new_creator_id)
            if creator and creator.username:
                who = display_user(creator.username, result.new_creator_id, lang)
            await callback.message.answer(t(lang, "group_transferred", who=who))
            if creator and creator.notifications and creator.started_at:
                try:
                    await bot.send_message(creator.id, t(creator.lang, "group_heir_now", who=who))
                except Exception:
                    pass
        else:
            await callback.message.answer(t(lang, "group_left", name=result.group_name))
        await callback.answer()
        return
    if action == "heir" and group is not None:
        await _show_heir_list(callback, session, db_user, group)
        return
    if action == "appoint" and extra and extra.isdigit() and group is not None:
        status = await set_successor(session, group_id, db_user.id, int(extra))
        if status != "ok":
            await callback.answer(t(lang, "group_not_creator" if status == "not_creator" else "group_not_member"), show_alert=True)
            return
        user = await repo.get_user(session, int(extra))
        who = display_user(user.username if user else None, int(extra), lang)
        await callback.message.answer(t(lang, "group_heir_set", who=who))
        await callback.answer()
        return
    if action == "transfer" and extra and extra.isdigit() and group is not None:
        status = await transfer_now(session, group_id, db_user.id, int(extra))
        if status != "ok":
            await callback.answer(t(lang, "group_not_creator"), show_alert=True)
            return
        user = await repo.get_user(session, int(extra))
        who = display_user(user.username if user else None, int(extra), lang)
        await callback.message.answer(t(lang, "group_heir_now", who=who))
        await callback.answer()
        return
    if action == "hist" and group is not None:
        await _show_history(callback, session, db_user, group)
        return
    await callback.answer()


async def _show_remove_list(callback, session, db_user, group) -> None:
    if group.creator_id != db_user.id:
        await callback.answer(t(db_user.lang, "group_not_creator"), show_alert=True)
        return
    members = [member for member in await repo.list_members(session, group.id) if member.user_id != db_user.id]
    if not members:
        await callback.answer(t(db_user.lang, "group_no_other_members"), show_alert=True)
        return
    rows = []
    for member in members[:30]:
        label = display_user(member.username, member.user_id, db_user.lang)
        rows.append([ButtonSpec(label, callback_data=f"grp:rm:{group.id}:{member.user_id}")])
    rows.append([ButtonSpec(t(db_user.lang, "back"), callback_data=f"grp:view:{group.id}")])
    await edit_or_send(callback, t(db_user.lang, "group_pick_member"), markup(rows))


async def _show_heir_list(callback, session, db_user, group) -> None:
    if group.creator_id != db_user.id:
        await callback.answer(t(db_user.lang, "group_not_creator"), show_alert=True)
        return
    members = [member for member in await repo.list_members(session, group.id) if member.user_id != db_user.id]
    if not members:
        await callback.answer(t(db_user.lang, "group_no_other_members"), show_alert=True)
        return
    rows = []
    for member in members[:20]:
        label = display_user(member.username, member.user_id, db_user.lang)
        rows.append(
            [
                ButtonSpec(t(db_user.lang, "appoint"), callback_data=f"grp:appoint:{group.id}:{member.user_id}"),
                ButtonSpec(t(db_user.lang, "transfer_now"), callback_data=f"grp:transfer:{group.id}:{member.user_id}"),
            ]
        )
        rows.append([ButtonSpec(label, callback_data="noop")])
    rows.append([ButtonSpec(t(db_user.lang, "back"), callback_data=f"grp:view:{group.id}")])
    await edit_or_send(callback, t(db_user.lang, "choose_heir"), markup(rows))


async def _show_history(callback, session, db_user, group) -> None:
    if not await repo.is_member(session, group.id, db_user.id):
        await callback.answer(t(db_user.lang, "only_members"), show_alert=True)
        return
    whispers = await repo.list_group_whispers(session, group.id, limit=10)
    if not whispers:
        await callback.answer(t(db_user.lang, "group_history_empty"), show_alert=True)
        return
    rows = []
    for whisper in whispers:
        label = t(
            db_user.lang,
            "history_item",
            when=format_human(whisper.created_at),
            preview=clip(whisper.text or whisper.media_type or whisper.status, 24),
        )
        rows.append([ButtonSpec(clip_button(label, 60), callback_data=f"open:{whisper.token}")])
    rows.append([ButtonSpec(t(db_user.lang, "back"), callback_data=f"grp:view:{group.id}")])
    await edit_or_send(callback, t(db_user.lang, "group_history_title", name=group.name), markup(rows))


async def _after_mutation(callback, session, db_user, bot_username, group_id, status: str, removed: bool = False) -> None:
    lang = db_user.lang
    if status == "not_creator":
        await callback.answer(t(lang, "group_not_creator"), show_alert=True)
        return
    if status == "dissolved":
        await callback.message.answer(t(lang, "group_dissolved", name=""))
        await callback.answer()
        return
    if status == "removed" and removed:
        await callback.message.answer(t(lang, "group_removed"))
    group = await repo.get_group(session, group_id)
    if group:
        await send_group_card(callback.message, session, db_user, bot_username, group)
    await callback.answer()


@router.message(GroupFlow.rename, F.text, ~F.text.startswith("/"))
async def rename_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    bot_username: str,
) -> None:
    data = await state.get_data()
    status = await rename_group(session, int(data["group_id"]), db_user.id, message.text)
    if status == "bad_name":
        await message.answer(t(db_user.lang, "group_bad_name"))
        return
    await state.clear()
    if status != "ok":
        await message.answer(t(db_user.lang, "group_not_creator"))
        return
    group = await repo.get_group(session, int(data["group_id"]))
    await message.answer(t(db_user.lang, "group_renamed", name=group.name if group else message.text))
    if group:
        await send_group_card(message, session, db_user, bot_username, group)


@router.message(GroupFlow.add_member, F.text, ~F.text.startswith("/"))
async def add_text(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    db_user: User,
    bot,
    bot_username: str,
) -> None:
    data = await state.get_data()
    group_id = int(data["group_id"])
    result = await add_member(session, group_id, db_user.id, message.text, utcnow())
    if result.error == "bad":
        await message.answer(t(db_user.lang, "group_bad_target"))
        return
    if result.error == "already":
        await message.answer(t(db_user.lang, "group_already"))
        return
    if result.error:
        await state.clear()
        await message.answer(t(db_user.lang, "group_not_creator" if result.error == "not_creator" else "group_not_found"))
        return
    await state.clear()
    if result.invited_only:
        await message.answer(t(db_user.lang, "group_invited"))
    else:
        added_user = await repo.get_user(session, result.user_id) if result.user_id else None
        if added_user is None or added_user.started_at is None:
            await message.answer(t(db_user.lang, "group_invited"))
        else:
            await message.answer(t(db_user.lang, "group_added"))
            await notify_added(bot, session, [result.user_id], result.group_name)
    group = await repo.get_group(session, group_id)
    if group:
        await send_group_card(message, session, db_user, bot_username, group)
