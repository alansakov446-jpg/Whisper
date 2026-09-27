from __future__ import annotations

import re
from dataclasses import dataclass, field

from bot.constants import E2E_RE, INLINE_ID_RE, LABEL_RE, MAX_INLINE_IDS, MEDIA_ID_RE
from bot.services.ttl import DurationError, parse_duration, validate_ttl
from bot.utils.text import norm_username

MENTION_RE = re.compile(r"(?<![\w])@([A-Za-z][A-Za-z0-9_]{4,31})")
GROUP_RE = re.compile(r"(?:^|\s)(?:#|группа:|group:)([^\\s#@]{1,64})", re.IGNORECASE)
TTL_RE = re.compile(r"(?:^|\s)(?:ttl:|/ttl\s+)(\S+)\s*$", re.IGNORECASE)


@dataclass
class RecipientRef:
    """One resolved recipient reference from a query: @username or id=NUMBER."""

    kind: str  # "username" | "id"
    value: str | int


def parse_recipients(text: str | None) -> list[RecipientRef]:
    """Extract @mentions and ``id=NUMBER`` refs in document order.

    Bare digits in the text are ignored on purpose: only the explicit
    ``id=ЧИСЛО`` form counts in inline queries. Duplicates drop, order keeps,
    at most 50 recipients.
    """
    raw = text or ""
    hits: list[tuple[int, str, str | int]] = []
    for match in MENTION_RE.finditer(raw):
        name = norm_username(match.group(1))
        if name:
            hits.append((match.start(), "username", name))
    for match in INLINE_ID_RE.finditer(raw):
        hits.append((match.start(), "id", int(match.group(1))))
    hits.sort(key=lambda item: item[0])
    refs: list[RecipientRef] = []
    seen: set[tuple[str, object]] = set()
    for _pos, kind, value in hits:
        marker = (kind, value)
        if marker in seen:
            continue
        if len(refs) >= MAX_INLINE_IDS:
            continue
        seen.add(marker)
        refs.append(RecipientRef(kind, value))
    return refs


def parse_media_key(text: str | None) -> str | None:
    """Extract ``MEDIA_ID="KEY"`` from the whisper text. None when absent."""
    match = MEDIA_ID_RE.search(text or "")
    if not match:
        return None
    return match.group(1).upper()


def parse_label(text: str | None) -> str | None:
    match = LABEL_RE.search(text or "")
    return match.group(1) if match else None


def parse_sealed(text: str | None) -> bool:
    return bool(E2E_RE.search(text or ""))


def parse_text_without_meta(text: str | None) -> str:
    """Strip every meta token (mentions, id=, MEDIA_ID, group, ttl, label, e2e)."""
    working = text or ""
    working = TTL_RE.sub(" ", working)
    working = MEDIA_ID_RE.sub(" ", working)
    working = LABEL_RE.sub(" ", working)
    working = E2E_RE.sub(" ", working)
    working = MENTION_RE.sub(" ", working)
    working = INLINE_ID_RE.sub(" ", working)
    working = GROUP_RE.sub(" ", working)
    return re.sub(r"\s+", " ", working).strip()


@dataclass
class ParsedQuery:
    text: str
    usernames: list[str]
    user_ids: list[int]
    group_ref: str | None
    ttl_raw: str | None
    ttl_seconds: int | None
    ttl_explicit: bool
    ttl_error: str | None
    media_key: str | None = None
    label: str | None = None
    sealed: bool = False
    recipients: list[RecipientRef] = field(default_factory=list)


def parse_inline_query(raw: str | None, *, bot_username: str | None = None) -> ParsedQuery:
    original = raw or ""
    ttl_raw = None
    ttl_seconds = None
    ttl_explicit = False
    ttl_error = None
    working = original
    match = TTL_RE.search(original)
    if match:
        ttl_raw = match.group(1)
        working = (original[: match.start()] + original[match.end() :]).strip()
        try:
            parsed = parse_duration(ttl_raw, allow_forever=True)
            ttl_seconds = validate_ttl(parsed)
            ttl_explicit = True
        except DurationError:
            ttl_error = ttl_raw
    media_key = parse_media_key(working)
    label = parse_label(working)
    sealed = parse_sealed(working)
    refs = parse_recipients(working)
    bot_name = (bot_username or "").lower()
    usernames: list[str] = []
    user_ids: list[int] = []
    kept: list[RecipientRef] = []
    for ref in refs:
        if ref.kind == "username":
            if ref.value == bot_name:
                continue
            usernames.append(ref.value)
        else:
            user_ids.append(int(ref.value))
        kept.append(ref)
    group_match = GROUP_RE.search(working)
    group_ref = group_match.group(1).strip() if group_match else None
    text = parse_text_without_meta(working)
    return ParsedQuery(
        text=text,
        usernames=usernames,
        user_ids=user_ids,
        group_ref=group_ref,
        ttl_raw=ttl_raw,
        ttl_seconds=ttl_seconds,
        ttl_explicit=ttl_explicit,
        ttl_error=ttl_error,
        media_key=media_key,
        label=label,
        sealed=sealed,
        recipients=kept,
    )


@dataclass
class Identity:
    user_id: int | None = None
    username: str | None = None


def parse_identity(raw: str) -> Identity:
    """Parse a /send recipient field. The whole bare number is the ID."""
    text = (raw or "").strip()
    if not text:
        return Identity()
    lowered = text.lower()
    if lowered.startswith("id:") and lowered[3:].strip().isdigit():
        return Identity(user_id=int(lowered[3:].strip()))
    if lowered.startswith("id=") and lowered[3:].strip().isdigit():
        return Identity(user_id=int(lowered[3:].strip()))
    username = norm_username(text)
    if username:
        return Identity(username=username)
    if text.isdigit() and 1 <= len(text) <= 15:
        return Identity(user_id=int(text))
    return Identity()
