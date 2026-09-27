from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.services.deeplink import group_link, ttl_link, whisper_link
from bot.services.i18n import format_ttl, t
from bot.utils.text import clip
from bot.utils.time import format_human


@dataclass
class ButtonSpec:
    text: str
    callback_data: str | None = None
    url: str | None = None


def _mark(selected: bool, text: str) -> str:
    return f"✅ {text}" if selected else text


def envelope_text(
    *,
    lang: str,
    recipient_username: str | None = None,
    recipient_id: int | None = None,
    group_name: str | None = None,
    group_size: int | None = None,
    is_group: bool = False,
    is_anonymous: bool = False,
    pseudonym: str | None = None,
    sender_username: str | None = None,
    delivery_mode: str = "inline",
    ttl_seconds: int | None = None,
    scheduled_for: datetime | None = None,
    label: str | None = None,
) -> str:
    lines: list[str] = []
    if is_group:
        lines.append(t(lang, "envelope_group", name=group_name or "—", n=group_size or 0))
        if is_anonymous:
            lines.append(t(lang, "envelope_anonymous"))
        elif pseudonym:
            lines.append(t(lang, "envelope_from_pseudo", name=pseudonym))
    elif is_anonymous:
        lines.append(t(lang, "envelope_anonymous"))
    elif delivery_mode == "open":
        if pseudonym:
            lines.append(t(lang, "envelope_from_pseudo", name=pseudonym))
        elif sender_username:
            lines.append(t(lang, "envelope_from_user", username=sender_username))
        elif recipient_username:
            lines.append(t(lang, "envelope_only_for", username=recipient_username))
        elif recipient_id:
            lines.append(t(lang, "envelope_only_for_id", id=recipient_id))
        else:
            lines.append(t(lang, "opened_header"))
    else:
        if pseudonym:
            lines.append(t(lang, "envelope_from_pseudo", name=pseudonym))
        if recipient_username:
            lines.append(t(lang, "envelope_only_for", username=recipient_username))
        elif recipient_id:
            lines.append(t(lang, "envelope_only_for_id", id=recipient_id))
        elif not pseudonym:
            lines.append(t(lang, "opened_header"))
    lines.append(t(lang, "envelope_ttl", ttl=format_ttl(ttl_seconds, lang)))
    if scheduled_for is not None:
        lines.append(t(lang, "scheduled_for", when=format_human(scheduled_for).replace(" UTC", "")))
    if label and lines and lines[0].startswith("🔒"):
        lines[0] = label + lines[0][len("🔒") :]
    elif label and lines:
        lines[0] = f"{label} {lines[0]}"
    return "\n".join(lines)


def envelope_buttons(
    *,
    lang: str,
    token: str,
    bot_username: str,
    has_media: bool,
    include_ttl: bool = True,
) -> list[list[ButtonSpec]]:
    if has_media:
        primary = ButtonSpec(t(lang, "open_in_bot"), url=whisper_link(bot_username, token))
    else:
        primary = ButtonSpec(t(lang, "open_button"), callback_data=f"open:{token}")
    rows = [[primary]]
    if include_ttl:
        rows.append([ButtonSpec(t(lang, "ttl_button"), url=ttl_link(bot_username, token))])
    return rows


def main_menu(lang: str) -> list[list[ButtonSpec]]:
    return [
        [
            ButtonSpec(t(lang, "menu_send"), callback_data="menu:send"),
            ButtonSpec(t(lang, "menu_settings"), callback_data="menu:settings"),
        ],
        [
            ButtonSpec(t(lang, "menu_contacts"), callback_data="menu:contacts"),
            ButtonSpec(t(lang, "menu_groups"), callback_data="menu:groups"),
        ],
        [ButtonSpec(t(lang, "menu_help"), callback_data="menu:help")],
    ]


def settings_text(lang: str, user) -> str:
    pseudo = user.pseudonym or t(lang, "pseudo_empty")
    quiet_value = (
        f"{user.quiet_from}-{user.quiet_to}" if getattr(user, "quiet_from", None) and getattr(user, "quiet_to", None) else t(lang, "quiet_state_off")
    )
    return "\n".join(
        [
            t(lang, "settings_title"),
            "",
            t(lang, "settings_ttl") + " " + format_ttl(user.default_ttl, lang),
            t(lang, "settings_mode"),
            t(lang, "settings_pseudo", value=pseudo),
            t(lang, "settings_lang", code=t(lang, "lang_ru") if lang != "en" else t(lang, "lang_en")),
            t(lang, "quiet_title", value=quiet_value),
            "",
            t(lang, "settings_intro"),
        ]
    )


def settings_keyboard(lang: str, user) -> list[list[ButtonSpec]]:
    current = user.default_ttl
    presets = [
        (None, "ttl_forever", "inf"),
        (60, "ttl_1m", "60"),
        (3600, "ttl_1h", "3600"),
        (86400, "ttl_24h", "86400"),
        (604800, "ttl_7d", "604800"),
    ]
    row1 = [
        ButtonSpec(_mark(current == seconds, t(lang, key)), callback_data=f"set:ttl:{code}")
        for seconds, key, code in presets[:3]
    ]
    row2 = [
        ButtonSpec(_mark(current == seconds, t(lang, key)), callback_data=f"set:ttl:{code}")
        for seconds, key, code in presets[3:]
    ]
    row2.append(ButtonSpec(t(lang, "ttl_custom"), callback_data="set:ttl:custom"))
    mode = user.default_mode
    notif = t(lang, "settings_notif_on" if user.notifications else "settings_notif_off")
    seal = t(lang, "settings_seal_on" if getattr(user, "seal_default", False) else "settings_seal_off")
    return [
        row1,
        row2,
        [
            ButtonSpec(_mark(mode == "anonymous", t(lang, "settings_mode_anon")), callback_data="set:mode:anonymous"),
            ButtonSpec(_mark(mode == "open", t(lang, "settings_mode_open")), callback_data="set:mode:open"),
        ],
        [ButtonSpec(notif, callback_data="set:notif")],
        [
            ButtonSpec(_mark(lang != "en", t(lang, "lang_ru")), callback_data="set:lang:ru"),
            ButtonSpec(_mark(lang == "en", t(lang, "lang_en")), callback_data="set:lang:en"),
        ],
        [ButtonSpec(t(lang, "settings_pseudo", value=user.pseudonym or t(lang, "pseudo_empty")), callback_data="set:pseudo")],
        [ButtonSpec(seal, callback_data="set:seal")],
        [ButtonSpec(t(lang, "quiet_title", value=_quiet_value(user, lang)), callback_data="set:quiet")],
        [ButtonSpec(t(lang, "back"), callback_data="menu:main")],
    ]


def _quiet_value(user, lang: str) -> str:
    if getattr(user, "quiet_from", None) and getattr(user, "quiet_to", None):
        return f"{user.quiet_from}-{user.quiet_to}"
    return t(lang, "quiet_state_off")


def ttl_choice_keyboard(lang: str, token: str, current: int | None) -> list[list[ButtonSpec]]:
    options = [
        (None, "ttl_forever", "inf"),
        (60, "ttl_1m", "60"),
        (3600, "ttl_1h", "3600"),
        (86400, "ttl_24h", "86400"),
        (604800, "ttl_7d", "604800"),
    ]
    rows: list[list[ButtonSpec]] = []
    buffer: list[ButtonSpec] = []
    for seconds, key, code in options:
        buffer.append(
            ButtonSpec(_mark(current == seconds, t(lang, key)), callback_data=f"ttlset:{token}:{code}")
        )
        if len(buffer) == 3:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)
    rows.append([ButtonSpec(t(lang, "ttl_custom"), callback_data=f"ttlset:{token}:custom")])
    rows.append([ButtonSpec(t(lang, "back"), callback_data="menu:main")])
    return rows


def send_ttl_keyboard(lang: str, current: int | None) -> list[list[ButtonSpec]]:
    options = [
        (None, "ttl_forever", "inf"),
        (60, "ttl_1m", "60"),
        (3600, "ttl_1h", "3600"),
        (86400, "ttl_24h", "86400"),
        (604800, "ttl_7d", "604800"),
    ]
    rows: list[list[ButtonSpec]] = []
    buffer: list[ButtonSpec] = []
    for seconds, key, code in options:
        buffer.append(ButtonSpec(_mark(current == seconds, t(lang, key)), callback_data=f"send:ttl:{code}"))
        if len(buffer) == 3:
            rows.append(buffer)
            buffer = []
    if buffer:
        rows.append(buffer)
    rows.append([ButtonSpec(t(lang, "ttl_custom"), callback_data="send:ttl:custom")])
    return rows


def send_mode_keyboard(lang: str, current: str) -> list[list[ButtonSpec]]:
    return [
        [
            ButtonSpec(_mark(current == "anonymous", t(lang, "settings_mode_anon")), callback_data="send:mode:anonymous"),
            ButtonSpec(_mark(current == "open", t(lang, "settings_mode_open")), callback_data="send:mode:open"),
        ]
    ]


def pager_buttons(token: str, page: int, pages: int) -> list[list[ButtonSpec]]:
    if pages <= 1:
        return []
    row: list[ButtonSpec] = []
    if page > 0:
        row.append(ButtonSpec("◀️", callback_data=f"pg:{token}:{page - 1}"))
    row.append(ButtonSpec(f"{page + 1}/{pages}", callback_data="noop"))
    if page < pages - 1:
        row.append(ButtonSpec("▶️", callback_data=f"pg:{token}:{page + 1}"))
    return [row]


def group_card_text(lang: str, group, members, creator_name: str, heir_name: str | None, bot_username: str) -> str:
    lines = [
        f"👥 {group.name}",
        t(lang, "creator_line", name=creator_name),
        t(lang, "created_line", when=format_human(group.created_at)),
        t(lang, "heir_line", name=heir_name or t(lang, "heir_none")),
        t(lang, "members_line", n=len(members)),
    ]
    if members:
        for member in members:
            label = f"@{member.username}" if member.username else t(lang, "id_label", id=member.user_id)
            if member.user_id == group.creator_id:
                label += " 👑"
            lines.append(f"• {label}")
    else:
        lines.append("• —")
    lines.append(t(lang, "link_line", link=group_link(bot_username, group.id)))
    lines.append(t(lang, "group_link_hint"))
    return "\n".join(lines)


def group_buttons(lang: str, group, viewer_id: int) -> list[list[ButtonSpec]]:
    gid = group.id
    if group.creator_id == viewer_id:
        return [
            [
                ButtonSpec(t(lang, "group_rename"), callback_data=f"grp:rename:{gid}"),
                ButtonSpec(t(lang, "group_remove"), callback_data=f"grp:rmlist:{gid}"),
            ],
            [
                ButtonSpec(t(lang, "group_add"), callback_data=f"grp:add:{gid}"),
                ButtonSpec(t(lang, "group_heir"), callback_data=f"grp:heir:{gid}"),
            ],
            [
                ButtonSpec(t(lang, "group_leave"), callback_data=f"grp:leave:{gid}"),
                ButtonSpec(t(lang, "group_history"), callback_data=f"grp:hist:{gid}"),
            ],
            [
                ButtonSpec(t(lang, "group_export"), callback_data=f"grp:export:{gid}"),
                ButtonSpec(t(lang, "group_autojoin"), callback_data=f"grp:autojoin:{gid}"),
            ],
            [ButtonSpec(t(lang, "group_back"), callback_data="menu:groups")],
        ]
    return [
        [ButtonSpec(t(lang, "group_leave"), callback_data=f"grp:leave:{gid}")],
        [ButtonSpec(t(lang, "group_history"), callback_data=f"grp:hist:{gid}")],
        [ButtonSpec(t(lang, "group_back"), callback_data="menu:groups")],
    ]


def display_user(
    username: str | None,
    user_id: int | None,
    lang: str,
    first_name: str | None = None,
    last_name: str | None = None,
) -> str:
    if username:
        return f"@{username}"
    name = " ".join(part for part in (first_name, last_name) if part).strip()
    if name:
        return name
    if user_id:
        return t(lang, "id_link", id=user_id)
    return "—"


def contact_star(favorite: bool) -> str:
    return "⭐ " if favorite else ""


def label_keyboard(callback_prefix: str = "send:label") -> list[list[ButtonSpec]]:
    from bot.constants import ENVELOPE_LABELS

    return [[ButtonSpec(label, callback_data=f"{callback_prefix}:{label}") for label in ENVELOPE_LABELS]]


def open_actions_keyboard(lang: str, token: str, pinned: bool = False) -> list[list[ButtonSpec]]:
    return [
        [
            ButtonSpec(t(lang, "reply_button"), callback_data=f"reply:{token}"),
            ButtonSpec(
                t(lang, "unpin_button" if pinned else "pin_button"),
                callback_data=f"unpin:{token}" if pinned else f"pin:{token}",
            ),
        ]
    ]


def sender_actions_keyboard(lang: str, token: str, can_extend: bool = True) -> list[list[ButtonSpec]]:
    rows: list[list[ButtonSpec]] = []
    if can_extend:
        rows.append(
            [
                ButtonSpec(t(lang, "extend_1h"), callback_data=f"wact:{token}:ext:3600"),
                ButtonSpec(t(lang, "extend_24h"), callback_data=f"wact:{token}:ext:86400"),
            ]
        )
    rows.append([ButtonSpec(t(lang, "web_button"), callback_data=f"web:{token}")])
    rows.append([ButtonSpec(t(lang, "delete_button"), callback_data=f"wact:{token}:del")])
    return rows


def clip_button(text: str, limit: int = 60) -> str:
    return clip(text, limit)
