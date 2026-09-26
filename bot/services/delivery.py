from __future__ import annotations

from dataclasses import dataclass, field

from loguru import logger

from bot.db import repo
from bot.services.i18n import t
from bot.services.views import envelope_buttons, envelope_text
from bot.services.whisper_service import add_delivery_ref, split_caption
from bot.utils.time import as_naive_utc


@dataclass
class DeliveryReport:
    sent: int = 0
    failed: int = 0
    need_link: bool = False
    link: str = ""


def outgoing_envelope(whisper, lang: str, bot_username: str) -> tuple[str, object]:
    from bot.keyboards.builders import markup

    size = None
    text = envelope_text(
        lang=lang,
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
        scheduled_for=as_naive_utc(whisper.send_at) if whisper.status == "scheduled" else None,
    )
    buttons = envelope_buttons(
        lang=lang,
        token=whisper.token,
        bot_username=bot_username,
        has_media=bool(whisper.media_file_id),
    )
    return text, markup(buttons)


async def send_media(bot, chat_id: int, media_type: str, file_id: str, caption: str | None, reply_markup=None):
    common = {"protect_content": True}
    if media_type == "photo":
        return await bot.send_photo(chat_id, file_id, caption=caption, reply_markup=reply_markup, **common)
    if media_type == "video":
        return await bot.send_video(chat_id, file_id, caption=caption, reply_markup=reply_markup, **common)
    if media_type == "document":
        return await bot.send_document(chat_id, file_id, caption=caption, reply_markup=reply_markup, **common)
    if media_type == "animation":
        return await bot.send_animation(chat_id, file_id, caption=caption, reply_markup=reply_markup, **common)
    if media_type == "voice":
        return await bot.send_voice(chat_id, file_id, caption=caption, reply_markup=reply_markup, **common)
    if media_type == "audio":
        return await bot.send_audio(chat_id, file_id, caption=caption, reply_markup=reply_markup, **common)
    if media_type == "video_note":
        message = await bot.send_video_note(chat_id, file_id, protect_content=True)
        if caption:
            await bot.send_message(chat_id, caption, protect_content=True, reply_markup=reply_markup)
        return message
    raise ValueError(media_type)


async def send_opened_content(bot, chat_id: int, plan, token: str, lang: str):
    from bot.keyboards.builders import markup
    from bot.services.views import pager_buttons

    keyboard = markup(pager_buttons(token, plan.page, plan.pages))
    if plan.media_type and plan.media_file_id:
        caption, overflow = split_caption(plan.header, plan.body)
        await send_media(bot, chat_id, plan.media_type, plan.media_file_id, caption, keyboard if not overflow else None)
        if overflow:
            await bot.send_message(chat_id, overflow, protect_content=True, reply_markup=keyboard)
        return
    await bot.send_message(chat_id, plan.page_text or plan.message, protect_content=True, reply_markup=keyboard)


async def deliver_outgoing(bot, session, whisper, bot_username: str) -> DeliveryReport:
    from bot.services.deeplink import whisper_link

    report = DeliveryReport(link=whisper_link(bot_username, whisper.token))
    lang = whisper.lang or "ru"
    if whisper.is_group and whisper.group_id:
        members = await repo.list_members(session, whisper.group_id)
        text, keyboard = outgoing_envelope(whisper, lang, bot_username)
        # group size was unknown at envelope build; rebuild with count
        from bot.services.views import envelope_text
        from bot.keyboards.builders import markup
        from bot.services.views import envelope_buttons

        text = envelope_text(
            lang=lang,
            group_name=whisper.group_name,
            group_size=len(members),
            is_group=True,
            is_anonymous=whisper.is_anonymous,
            pseudonym=whisper.pseudonym_snapshot,
            sender_username=whisper.sender_username,
            delivery_mode=whisper.delivery_mode,
            ttl_seconds=whisper.ttl_seconds,
        )
        keyboard = markup(
            envelope_buttons(
                lang=lang,
                token=whisper.token,
                bot_username=bot_username,
                has_media=bool(whisper.media_file_id),
            )
        )
        for member in members:
            if whisper.sender_id and member.user_id == whisper.sender_id:
                continue
            try:
                message = await bot.send_message(member.user_id, text, protect_content=True, reply_markup=keyboard)
                add_delivery_ref(whisper, chat_id=member.user_id, message_id=message.message_id)
                report.sent += 1
            except Exception as exc:
                report.failed += 1
                logger.info("group deliver failed for {}: {}", member.user_id, type(exc).__name__)
        if report.failed:
            report.need_link = True
        return report

    text, keyboard = outgoing_envelope(whisper, lang, bot_username)
    target = whisper.recipient_id
    if not target:
        report.need_link = True
        return report
    try:
        message = await bot.send_message(target, text, protect_content=True, reply_markup=keyboard)
        add_delivery_ref(whisper, chat_id=target, message_id=message.message_id)
        report.sent = 1
    except Exception as exc:
        report.failed = 1
        report.need_link = True
        logger.info("deliver failed for {}: {}", target, type(exc).__name__)
    return report


def sender_result_text(lang: str, report: DeliveryReport, *, scheduled: bool, when: str, group: bool) -> str:
    if scheduled:
        return t(lang, "send_scheduled", when=when)
    lines = []
    if group:
        lines.append(t(lang, "send_group_done", n=report.sent))
        if report.need_link:
            lines.append(t(lang, "send_group_partial", link=report.link))
    else:
        lines.append(t(lang, "send_done"))
        if report.need_link:
            lines.append(t(lang, "send_link", link=report.link))
    return "\n\n".join(lines)
