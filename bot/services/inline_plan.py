from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from bot.constants import TOKEN_BYTES
from bot.services.i18n import format_ttl, t
from bot.services.parser import ParsedQuery
from bot.services.ttl import resolve_ttl
from bot.services.views import ButtonSpec, envelope_buttons, envelope_text
from bot.utils.text import clip


@dataclass
class DraftSpec:
    token: str
    text: str | None
    recipient_id: int | None = None
    recipient_username: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    is_group: bool = False
    new_group_usernames: list[str] = field(default_factory=list)
    new_group_ids: list[int] = field(default_factory=list)
    new_group_name: str | None = None
    ttl_seconds: int | None = None
    media_type: str | None = None
    media_file_id: str | None = None
    group_size: int = 0


@dataclass
class InlineArticle:
    result_id: str
    title: str
    description: str
    message_text: str
    buttons: list[list[ButtonSpec]]
    draft: DraftSpec | None = None


@dataclass
class InlinePlan:
    articles: list[InlineArticle]
    needs_activation: bool = False
    button_text: str | None = None
    button_start: str | None = None


def _token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


def _instruction(lang: str, bot_username: str) -> InlineArticle:
    return InlineArticle(
        result_id="help",
        title=t(lang, "inline_instruction"),
        description=t(lang, "inline_instruction_desc"),
        message_text=t(lang, "inline_type_text"),
        buttons=[[ButtonSpec(t(lang, "inline_instruction"), url=f"https://t.me/{bot_username}?start=help")]],
    )


def _send_article(
    *,
    lang: str,
    bot_username: str,
    title: str,
    description: str,
    draft: DraftSpec,
    pseudonym: str | None,
    sender_username: str | None,
) -> InlineArticle:
    text = envelope_text(
        lang=lang,
        recipient_username=draft.recipient_username,
        recipient_id=draft.recipient_id,
        group_name=draft.group_name or draft.new_group_name,
        group_size=draft.group_size,
        is_group=draft.is_group,
        pseudonym=pseudonym,
        sender_username=sender_username,
        delivery_mode="inline",
        ttl_seconds=draft.ttl_seconds,
    )
    buttons = envelope_buttons(
        lang=lang,
        token=draft.token,
        bot_username=bot_username,
        has_media=bool(draft.media_file_id),
    )
    return InlineArticle(
        result_id=draft.token,
        title=clip(title, 120),
        description=clip(description, 250),
        message_text=text,
        buttons=buttons,
        draft=draft,
    )


def _variants(base: DraftSpec, lang: str, bot_username: str, title_for, description: str, pseudonym, sender_username) -> list[InlineArticle]:
    articles = []
    for seconds in (60, 3600, 86400, 604800, None):
        draft = DraftSpec(**{**base.__dict__, "token": _token(), "ttl_seconds": seconds})
        draft.new_group_usernames = list(base.new_group_usernames)
        draft.new_group_ids = list(base.new_group_ids)
        articles.append(
            _send_article(
                lang=lang,
                bot_username=bot_username,
                title=title_for(format_ttl(seconds, lang)),
                description=description,
                draft=draft,
                pseudonym=pseudonym,
                sender_username=sender_username,
            )
        )
    return articles


def build_inline_plan(
    parsed: ParsedQuery,
    *,
    lang: str,
    activated: bool,
    rate_limited: bool,
    bot_username: str,
    sender_username: str | None,
    pseudonym: str | None,
    default_ttl: int | None,
    saved_usernames: set[str],
    saved_ids: set[int],
    contacts: list[tuple[str | None, int | None]],
    groups: list[tuple[int, str, int]],
    media_type: str | None = None,
    media_file_id: str | None = None,
    group_match: tuple[int, str, int] | None = None,
) -> InlinePlan:
    if not activated:
        return InlinePlan(
            articles=[
                InlineArticle(
                    result_id="activate",
                    title=t(lang, "activate_title"),
                    description=t(lang, "activate_button"),
                    message_text=t(lang, "activate_body"),
                    buttons=[[ButtonSpec(t(lang, "activate_button"), url=f"https://t.me/{bot_username}?start=inline")]],
                )
            ],
            needs_activation=True,
            button_text=t(lang, "activate_button"),
            button_start="inline",
        )
    if rate_limited:
        return InlinePlan(
            articles=[
                InlineArticle(
                    result_id="limit",
                    title=t(lang, "limit_article"),
                    description=t(lang, "rate_limit_short"),
                    message_text=t(lang, "rate_limit"),
                    buttons=[],
                )
            ]
        )
    if parsed.ttl_error:
        return InlinePlan(
            articles=[
                InlineArticle(
                    result_id="badttl",
                    title=t(lang, "inline_bad_ttl", value=parsed.ttl_raw or ""),
                    description=t(lang, "ttl_bad"),
                    message_text=t(lang, "inline_bad_ttl", value=parsed.ttl_raw or ""),
                    buttons=[],
                ),
                _instruction(lang, bot_username),
            ]
        )

    has_payload = bool(parsed.text) or bool(media_file_id)
    articles: list[InlineArticle] = []
    explicit = parsed.ttl_explicit
    chosen_ttl = parsed.ttl_seconds if explicit else None
    resolved = resolve_ttl(explicit=explicit, chosen=chosen_ttl, default=default_ttl)

    recipient_count = len(parsed.usernames) + len(parsed.user_ids)
    if recipient_count == 1 and has_payload:
        username = parsed.usernames[0] if parsed.usernames else None
        user_id = parsed.user_ids[0] if parsed.user_ids else None
        saved = (username in saved_usernames) or (user_id in saved_ids if user_id else False)
        description = t(lang, "inline_saved_contact" if saved else "inline_new_contact")
        if media_file_id:
            description = t(lang, "open_in_bot") + " · " + description
        title = (
            t(lang, "inline_title_user", username=username)
            if username
            else t(lang, "inline_title_id", id=user_id)
        )
        draft = DraftSpec(
            token=_token(),
            text=parsed.text or None,
            recipient_id=user_id,
            recipient_username=username,
            ttl_seconds=resolved,
            media_type=media_type,
            media_file_id=media_file_id,
        )
        articles.append(
            _send_article(
                lang=lang,
                bot_username=bot_username,
                title=title,
                description=description,
                draft=draft,
                pseudonym=pseudonym,
                sender_username=sender_username,
            )
        )
        if not explicit:
            def title_for(label: str, username=username, user_id=user_id) -> str:
                if username:
                    return t(lang, "inline_ttl_variant", ttl=label, username=username)
                return t(lang, "inline_ttl_variant_id", ttl=label, id=user_id)

            articles.extend(_variants(draft, lang, bot_username, title_for, description, pseudonym, sender_username))
        articles.append(_instruction(lang, bot_username))
        return InlinePlan(articles=articles[:50])

    if recipient_count > 1 and has_payload:
        names = list(parsed.usernames)
        ids = list(parsed.user_ids)
        size = recipient_count + 1
        shown = ", ".join(f"@{name}" for name in names[:3])
        if ids:
            shown = (shown + ", " if shown else "") + ", ".join(f"ID {item}" for item in ids[:2])
        group_name = clip(f"Шёпот: {shown}" if lang != "en" else f"Whisper: {shown}", 64)
        description = t(lang, "inline_create_group")
        if media_file_id:
            description = t(lang, "open_in_bot") + " · " + description
        title = t(lang, "inline_title_group", n=size)
        draft = DraftSpec(
            token=_token(),
            text=parsed.text or None,
            is_group=True,
            new_group_usernames=names,
            new_group_ids=ids,
            new_group_name=group_name,
            group_name=group_name,
            group_size=size,
            ttl_seconds=resolved,
            media_type=media_type,
            media_file_id=media_file_id,
        )
        articles.append(
            _send_article(
                lang=lang,
                bot_username=bot_username,
                title=title,
                description=description,
                draft=draft,
                pseudonym=pseudonym,
                sender_username=sender_username,
            )
        )
        if not explicit:
            articles.extend(
                _variants(
                    draft,
                    lang,
                    bot_username,
                    lambda label, size=size: t(lang, "inline_ttl_variant_group", ttl=label, n=size),
                    description,
                    pseudonym,
                    sender_username,
                )
            )
        articles.append(_instruction(lang, bot_username))
        return InlinePlan(articles=articles[:50])

    if parsed.group_ref:
        if group_match is None:
            return InlinePlan(
                articles=[
                    InlineArticle(
                        result_id="gmissing",
                        title=t(lang, "inline_group_missing", name=parsed.group_ref),
                        description=t(lang, "groups_empty").split("\n")[0],
                        message_text=t(lang, "inline_group_missing", name=parsed.group_ref),
                        buttons=[],
                    )
                ]
            )
        gid, name, size = group_match
        if not has_payload:
            return InlinePlan(
                articles=[
                    InlineArticle(
                        result_id=f"ghint{gid}",
                        title=t(lang, "inline_send_group", name=name, n=size),
                        description=t(lang, "inline_empty_text"),
                        message_text=t(lang, "inline_hint_group", name=name),
                        buttons=[],
                    )
                ]
            )
        description = t(lang, "inline_saved_contact")
        draft = DraftSpec(
            token=_token(),
            text=parsed.text or None,
            group_id=gid,
            group_name=name,
            is_group=True,
            group_size=size,
            ttl_seconds=resolved,
            media_type=media_type,
            media_file_id=media_file_id,
        )
        articles.append(
            _send_article(
                lang=lang,
                bot_username=bot_username,
                title=t(lang, "inline_send_group", name=name, n=size),
                description=description,
                draft=draft,
                pseudonym=pseudonym,
                sender_username=sender_username,
            )
        )
        articles.append(_instruction(lang, bot_username))
        return InlinePlan(articles=articles)

    if has_payload:
        for username, user_id in contacts[:8]:
            if not username and not user_id:
                continue
            label = f"@{username}" if username else f"ID {user_id}"
            draft = DraftSpec(
                token=_token(),
                text=parsed.text or None,
                recipient_id=user_id,
                recipient_username=username,
                ttl_seconds=resolved,
                media_type=media_type,
                media_file_id=media_file_id,
            )
            articles.append(
                _send_article(
                    lang=lang,
                    bot_username=bot_username,
                    title=t(lang, "inline_title_user", username=username) if username else t(lang, "inline_title_id", id=user_id),
                    description=t(lang, "inline_saved_contact"),
                    draft=draft,
                    pseudonym=pseudonym,
                    sender_username=sender_username,
                )
            )
            _ = label
        for gid, name, size in groups[:5]:
            draft = DraftSpec(
                token=_token(),
                text=parsed.text or None,
                group_id=gid,
                group_name=name,
                is_group=True,
                group_size=size,
                ttl_seconds=resolved,
                media_type=media_type,
                media_file_id=media_file_id,
            )
            articles.append(
                _send_article(
                    lang=lang,
                    bot_username=bot_username,
                    title=t(lang, "inline_send_group", name=name, n=size),
                    description=t(lang, "inline_saved_contact"),
                    draft=draft,
                    pseudonym=pseudonym,
                    sender_username=sender_username,
                )
            )
        if not articles:
            articles.append(
                InlineArticle(
                    result_id="empty",
                    title=t(lang, "inline_empty_text") if not parsed.text else t(lang, "inline_type_text"),
                    description=t(lang, "inline_new_contact"),
                    message_text=t(lang, "inline_type_text"),
                    buttons=[],
                )
            )
        articles.append(_instruction(lang, bot_username))
        return InlinePlan(articles=articles[:50])

    articles.append(_instruction(lang, bot_username))
    for username, user_id in contacts[:8]:
        if username:
            articles.append(
                InlineArticle(
                    result_id=f"hintu{username}",
                    title=f"📇 @{username}",
                    description=t(lang, "inline_saved_contact"),
                    message_text=t(lang, "inline_hint_contact", username=username),
                    buttons=[],
                )
            )
    for gid, name, size in groups[:5]:
        articles.append(
            InlineArticle(
                result_id=f"hintg{gid}",
                title=t(lang, "group_btn", name=name, n=size),
                description=t(lang, "inline_hint_group", name=name),
                message_text=t(lang, "inline_hint_group", name=name),
                buttons=[],
            )
        )
    if len(articles) == 1:
        articles.insert(
            0,
            InlineArticle(
                result_id="type",
                title=t(lang, "inline_type_text"),
                description=t(lang, "inline_new_contact"),
                message_text=t(lang, "inline_type_text"),
                buttons=[],
            ),
        )
    return InlinePlan(articles=articles[:50])
