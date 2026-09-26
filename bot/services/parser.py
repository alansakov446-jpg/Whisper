from __future__ import annotations

import re
from dataclasses import dataclass

from bot.services.ttl import DurationError, parse_duration, validate_ttl
from bot.utils.text import norm_username

MENTION_RE = re.compile(r"(?<![\w])@([A-Za-z][A-Za-z0-9_]{4,31})")
ID_RE = re.compile(r"(?<!\w)id:(\d{1,15})(?!\d)", re.IGNORECASE)
GROUP_RE = re.compile(r"(?:^|\s)(?:#|группа:|group:)([^\s#@]{1,64})", re.IGNORECASE)
TTL_RE = re.compile(r"(?:^|\s)(?:ttl:|/ttl\s+)(\S+)\s*$", re.IGNORECASE)


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
    usernames: list[str] = []
    bot_name = (bot_username or "").lower()
    for found in MENTION_RE.finditer(working):
        name = norm_username(found.group(1))
        if not name or name == bot_name or name in usernames:
            continue
        usernames.append(name)
    user_ids: list[int] = []
    for found in ID_RE.finditer(working):
        uid = int(found.group(1))
        if uid not in user_ids:
            user_ids.append(uid)
    group_ref = None
    group_match = GROUP_RE.search(working)
    if group_match:
        group_ref = group_match.group(1).strip()
    text = MENTION_RE.sub(" ", working)
    text = ID_RE.sub(" ", text)
    text = GROUP_RE.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return ParsedQuery(
        text=text,
        usernames=usernames,
        user_ids=user_ids,
        group_ref=group_ref,
        ttl_raw=ttl_raw,
        ttl_seconds=ttl_seconds,
        ttl_explicit=ttl_explicit,
        ttl_error=ttl_error,
    )


@dataclass
class Identity:
    user_id: int | None = None
    username: str | None = None


def parse_identity(raw: str) -> Identity:
    text = (raw or "").strip()
    if not text:
        return Identity()
    lowered = text.lower()
    if lowered.startswith("id:") and lowered[3:].strip().isdigit():
        return Identity(user_id=int(lowered[3:].strip()))
    username = norm_username(text)
    if username:
        return Identity(username=username)
    if text.isdigit() and 1 <= len(text) <= 15:
        return Identity(user_id=int(text))
    return Identity()
