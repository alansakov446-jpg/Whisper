from __future__ import annotations

from aiogram import Router
from aiogram.types import (
    ChosenInlineResult,
    InlineQuery,
    InlineQueryResultArticle,
    InlineQueryResultsButton,
    InputTextMessageContent,
    LinkPreviewOptions,
)
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import User
from bot.keyboards.builders import markup
from bot.services.crypto import Cipher
from bot.services.inline_plan import InlinePlan, build_inline_plan
from bot.services.parser import parse_inline_query
from bot.services.whisper_service import NewWhisper, activate_draft, create_whisper, is_rate_limited
from bot.handlers.common import notify_added
from bot.services.i18n import t
from bot.utils.time import utcnow

router = Router(name="inline")


def plan_to_results(plan: InlinePlan) -> list[InlineQueryResultArticle]:
    results = []
    for article in plan.articles:
        kwargs = {}
        keyboard = markup(article.buttons)
        if keyboard is not None:
            kwargs["reply_markup"] = keyboard
        description = article.description or None
        results.append(
            InlineQueryResultArticle(
                id=article.result_id[:64],
                title=article.title[:120] or "…",
                description=description[:255] if description else None,
                input_message_content=InputTextMessageContent(
                    message_text=article.message_text[:4096],
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                ),
                **kwargs,
            )
        )
    return results


async def persist_drafts(session: AsyncSession, cipher: Cipher, db_user: User, plan: InlinePlan) -> None:
    for article in plan.articles:
        draft = article.draft
        if draft is None:
            continue
        pending = None
        if draft.new_group_usernames or draft.new_group_ids:
            pending = {
                "usernames": draft.new_group_usernames,
                "user_ids": draft.new_group_ids,
                "name": draft.new_group_name,
            }
        await create_whisper(
            session,
            cipher,
            NewWhisper(
                sender_id=db_user.id,
                sender_username=db_user.username,
                pseudonym=db_user.pseudonym,
                lang=db_user.lang,
                text=draft.text,
                recipient_id=draft.recipient_id,
                recipient_username=draft.recipient_username,
                group_id=draft.group_id,
                group_name=draft.group_name,
                is_group=draft.is_group or bool(pending),
                media_type=draft.media_type,
                media_file_id=draft.media_file_id,
                ttl_seconds=draft.ttl_seconds,
                ttl_explicit=True,
                delivery_mode="inline",
                status="draft",
                pending_targets=pending,
                token=draft.token,
            ),
        )


@router.inline_query()
async def on_inline(
    query: InlineQuery,
    session: AsyncSession,
    db_user: User | None,
    cipher: Cipher,
    bot_username: str,
) -> None:
    if db_user is None:
        await query.answer([])
        return
    parsed = parse_inline_query(query.query, bot_username=bot_username)
    contacts = await repo.list_contacts(session, db_user.id, limit=12)
    groups = await repo.list_user_groups(session, db_user.id)
    saved_usernames = {item.username for item in contacts if item.username}
    saved_ids = {item.contact_user_id for item in contacts if item.contact_user_id}
    group_match = None
    if parsed.group_ref:
        found = await repo.find_user_group_by_name(session, db_user.id, parsed.group_ref)
        if found:
            members = await repo.list_members(session, found.id)
            group_match = (found.id, found.name, len(members))
    media = await repo.get_pending_media(session, db_user.id)
    limited = False
    if db_user.started_at is not None:
        limited = await is_rate_limited(session, cipher.hmac_sender(db_user.id), utcnow())
    plan = build_inline_plan(
        parsed,
        lang=db_user.lang,
        activated=db_user.started_at is not None,
        rate_limited=limited,
        bot_username=bot_username,
        sender_username=db_user.username,
        pseudonym=db_user.pseudonym,
        default_ttl=db_user.default_ttl,
        saved_usernames=saved_usernames,
        saved_ids=saved_ids,
        contacts=[(item.username, item.contact_user_id) for item in contacts],
        groups=[(group.id, group.name, count) for group, count in groups],
        media_type=media.media_type if media else None,
        media_file_id=media.file_id if media else None,
        group_match=group_match,
    )
    if db_user.started_at is not None and not limited and not parsed.ttl_error:
        await persist_drafts(session, cipher, db_user, plan)
    button = None
    if plan.button_text and plan.button_start:
        button = InlineQueryResultsButton(text=plan.button_text, start_parameter=plan.button_start)
    await query.answer(plan_to_results(plan), cache_time=0, is_personal=True, button=button)


@router.chosen_inline_result()
async def on_chosen(
    chosen: ChosenInlineResult,
    session: AsyncSession,
    db_user: User,
    cipher: Cipher,
    bot,
) -> None:
    result = await activate_draft(session, chosen.result_id, chosen.inline_message_id, cipher, utcnow())
    if result.status == "limited" and chosen.inline_message_id:
        try:
            await bot.edit_message_text(
                t(db_user.lang, "rate_limit"),
                inline_message_id=chosen.inline_message_id,
                reply_markup=None,
            )
        except Exception:
            pass
        return
    if result.status == "destroyed" and chosen.inline_message_id:
        try:
            await bot.edit_message_text(
                t(db_user.lang, "destroyed"),
                inline_message_id=chosen.inline_message_id,
                reply_markup=None,
            )
        except Exception:
            pass
        return
    if result.new_member_ids and result.group_name:
        await notify_added(bot, session, result.new_member_ids, result.group_name)
