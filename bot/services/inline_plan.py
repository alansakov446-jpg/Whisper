from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from bot.constants import TOKEN_BYTES
from bot.services.i18n import format_ttl, t
from bot.services.parser import ParsedQuery
from bot.services.ttl import resolve_ttl
from bot.services.views import ButtonSpec, contact_star, display_user, envelope_buttons, envelope_text
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
    media_key: str | None = None
    label: str | None = None
    is_sealed: bool = False


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
        label=draft.label,
    )
    buttons = envelope_buttons(
        lang=lang,
        token=draft.token,
        bot_username=bot_username,
        has_media=bool(draft.media_file_id) or bool(draft.media_key),
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


def _person_label(
    kind: str,
    value: str | int,
    lang: str,
    id_users: dict[int, object] | None,
) -> str:
    if kind == "username":
        return f"@{value}"
    user = (id_users or {}).get(int(value))
    if user is not None:
        return display_user(
            getattr(user, "username", None),
            int(value),
            lang,
            getattr(user, "first_name", None),
            getattr(user, "last_name", None),
        )
    return t(lang, "id_link", id=value)


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
    id_users: dict[int, object] | None = None,
    media_key_status: str | None = None,
    contact_info: list[dict] | None = None,
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
    if media_key_status in {"missing", "not_yours"}:
        message = t(lang, "convert_missing" if media_key_status == "missing" else "convert_not_yours")
        return InlinePlan(
            articles=[
                InlineArticle(
                    result_id="mkey",
                    title=message,
                    description=t(lang, "convert_ask"),
                    message_text=message,
                    buttons=[],
                )
            ]
        )

    has_payload = bool(parsed.text) or bool(media_file_id) or bool(parsed.media_key)
    articles: list[InlineArticle] = []
    explicit = parsed.ttl_explicit
    chosen_ttl = parsed.ttl_seconds if explicit else None
    resolved = resolve_ttl(explicit=explicit, chosen=chosen_ttl, default=default_ttl)

    refs = list(parsed.recipients)
    if not refs:
        # Back-compat: callers that feed only usernames/ids lists.
        refs = []
        for name in parsed.usernames:
            refs.append(type("R", (), {"kind": "username", "value": name}))
        for uid in parsed.user_ids:
            refs.append(type("R", (), {"kind": "id", "value": uid}))

    known_refs: list[tuple[str, object]] = []
    unknown_ids: list[int] = []
    for ref in refs:
        if ref.kind == "id" and id_users is not None and id_users.get(int(ref.value)) is None:
            unknown_ids.append(int(ref.value))
            continue
        known_refs.append((ref.kind, ref.value))

    for uid in unknown_ids:
        articles.append(
            InlineArticle(
                result_id=f"nouser{uid}",
                title=t(lang, "inline_id_not_found", id=uid),
                description=t(lang, "inline_type_text"),
                message_text=t(lang, "inline_id_not_found", id=uid),
                buttons=[],
            )
        )

    def single_draft(kind: str, value, token: str | None = None) -> DraftSpec:
        return DraftSpec(
            token=token or _token(),
            text=parsed.text or None,
            recipient_id=int(value) if kind == "id" else None,
            recipient_username=value if kind == "username" else getattr(id_users.get(int(value)), "username", None) if id_users else None,
            ttl_seconds=resolved,
            media_type=media_type,
            media_file_id=media_file_id,
            media_key=parsed.media_key,
            label=parsed.label,
            is_sealed=parsed.sealed,
        )

    def single_title(kind: str, value) -> str:
        return t(lang, "inline_title_user", username=value) if kind == "username" else (
            t(lang, "inline_title_person", name=_person_label(kind, value, lang, id_users))
        )

    def single_description(kind: str, value) -> str:
        saved = (kind == "username" and value in saved_usernames) or (kind == "id" and int(value) in saved_ids)
        description = t(lang, "inline_saved_contact" if saved else "inline_new_contact")
        if media_file_id or parsed.media_key:
            description = t(lang, "open_in_bot") + " · " + description
        return description

    recipient_count = len(known_refs)
    if recipient_count == 1 and has_payload:
        kind, value = known_refs[0]
        draft = single_draft(kind, value)
        articles.append(
            _send_article(
                lang=lang,
                bot_username=bot_username,
                title=single_title(kind, value),
                description=single_description(kind, value),
                draft=draft,
                pseudonym=pseudonym,
                sender_username=sender_username,
            )
        )
        if not explicit:
            def title_for(label: str, kind=kind, value=value) -> str:
                if kind == "username":
                    return t(lang, "inline_ttl_variant", ttl=label, username=value)
                return t(lang, "inline_ttl_variant_person", ttl=label, name=_person_label(kind, value, lang, id_users))

            articles.extend(_variants(draft, lang, bot_username, title_for, single_description(kind, value), pseudonym, sender_username))
        articles.append(_instruction(lang, bot_username))
        return InlinePlan(articles=articles[:50])

    if recipient_count > 1:
        id_known_count = sum(1 for kind, _ in known_refs if kind == "id")
        names = [value for kind, value in known_refs if kind == "username"]
        ids = [int(value) for kind, value in known_refs if kind == "id"]
        size = recipient_count + 1
        if id_known_count >= 2:
            # Several id= recipients: group / only-last / only-first.
            shown_parts = [(_person_label(kind, value, lang, id_users)) for kind, value in known_refs[:4]]
            group_name = clip(f"Шёпот: {', '.join(shown_parts)}" if lang != "en" else f"Whisper: {', '.join(shown_parts)}", 64)
            group_draft = DraftSpec(
                token=_token(),
                text=parsed.text or None,
                is_group=True,
                new_group_usernames=list(names),
                new_group_ids=list(ids),
                new_group_name=group_name,
                group_name=group_name,
                group_size=size,
                ttl_seconds=resolved,
                media_type=media_type,
                media_file_id=media_file_id,
                media_key=parsed.media_key,
                label=parsed.label,
                is_sealed=parsed.sealed,
            )
            articles.append(
                _send_article(
                    lang=lang,
                    bot_username=bot_username,
                    title=t(lang, "inline_title_group", n=size),
                    description=t(lang, "inline_create_group"),
                    draft=group_draft,
                    pseudonym=pseudonym,
                    sender_username=sender_username,
                )
            )
            last_kind, last_value = known_refs[-1]
            first_kind, first_value = known_refs[0]
            articles.append(
                _send_article(
                    lang=lang,
                    bot_username=bot_username,
                    title=t(lang, "inline_only_last"),
                    description=single_title(last_kind, last_value),
                    draft=single_draft(last_kind, last_value),
                    pseudonym=pseudonym,
                    sender_username=sender_username,
                )
            )
            articles.append(
                _send_article(
                    lang=lang,
                    bot_username=bot_username,
                    title=t(lang, "inline_only_first"),
                    description=single_title(first_kind, first_value),
                    draft=single_draft(first_kind, first_value),
                    pseudonym=pseudonym,
                    sender_username=sender_username,
                )
            )
            articles.append(_instruction(lang, bot_username))
            return InlinePlan(articles=articles[:50])

        # Several @usernames (optionally plus a single id): one group card.
        shown = ", ".join(f"@{name}" for name in names[:3])
        if ids:
            shown = (shown + ", " if shown else "") + ", ".join(
                _person_label("id", item, lang, id_users) for item in ids[:2]
            )
        group_name = clip(f"Шёпот: {shown}" if lang != "en" else f"Whisper: {shown}", 64)
        description = t(lang, "inline_create_group")
        if media_file_id or parsed.media_key:
            description = t(lang, "open_in_bot") + " · " + description
        draft = DraftSpec(
            token=_token(),
            text=parsed.text or None,
            is_group=True,
            new_group_usernames=list(names),
            new_group_ids=list(ids),
            new_group_name=group_name,
            group_name=group_name,
            group_size=size,
            ttl_seconds=resolved,
            media_type=media_type,
            media_file_id=media_file_id,
            media_key=parsed.media_key,
            label=parsed.label,
            is_sealed=parsed.sealed,
        )
        articles.append(
            _send_article(
                lang=lang,
                bot_username=bot_username,
                title=t(lang, "inline_title_group", n=size),
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
            media_key=parsed.media_key,
            label=parsed.label,
            is_sealed=parsed.sealed,
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

    contact_cards = _contact_entries(contacts, contact_info, lang)

    if has_payload:
        if not unknown_ids:
            articles.insert(
                0,
                InlineArticle(
                    result_id="norecipient",
                    title=t(lang, "inline_no_recipient"),
                    description=t(lang, "inline_type_text"),
                    message_text=t(lang, "inline_no_recipient"),
                    buttons=[],
                ),
            )
        for username, user_id, label in contact_cards[:8]:
            draft = DraftSpec(
                token=_token(),
                text=parsed.text or None,
                recipient_id=user_id,
                recipient_username=username,
                ttl_seconds=resolved,
                media_type=media_type,
                media_file_id=media_file_id,
                media_key=parsed.media_key,
                label=parsed.label,
                is_sealed=parsed.sealed,
            )
            articles.append(
                _send_article(
                    lang=lang,
                    bot_username=bot_username,
                    title=t(lang, "inline_title_user", username=username) if username else t(lang, "inline_title_person", name=label),
                    description=t(lang, "inline_saved_contact"),
                    draft=draft,
                    pseudonym=pseudonym,
                    sender_username=sender_username,
                )
            )
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
                media_key=parsed.media_key,
                label=parsed.label,
                is_sealed=parsed.sealed,
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
                    title=t(lang, "inline_type_text") if not parsed.text else t(lang, "inline_type_text"),
                    description=t(lang, "inline_new_contact"),
                    message_text=t(lang, "inline_type_text"),
                    buttons=[],
                )
            )
        articles.append(_instruction(lang, bot_username))
        return InlinePlan(articles=articles[:50])

    articles.append(_instruction(lang, bot_username))
    for username, user_id, label in contact_cards[:8]:
        articles.append(
            InlineArticle(
                result_id=f"hintu{username or user_id}",
                title=f"📇 {label}",
                description=t(lang, "inline_saved_contact"),
                message_text=t(lang, "inline_hint_contact", username=username or label),
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


def _contact_entries(
    contacts: list[tuple[str | None, int | None]],
    contact_info: list[dict] | None,
    lang: str,
) -> list[tuple[str | None, int | None, str]]:
    entries: list[tuple[str | None, int | None, str]] = []
    if contact_info is not None:
        for item in contact_info:
            label = display_user(
                item.get("username"),
                item.get("user_id"),
                lang,
                item.get("first_name"),
                item.get("last_name"),
            )
            entries.append((item.get("username"), item.get("user_id"), contact_star(bool(item.get("favorite"))) + label))
        return entries
    for username, user_id in contacts:
        if not username and not user_id:
            continue
        label = f"@{username}" if username else t(lang, "id_label", id=user_id)
        entries.append((username, user_id, label))
    return entries
