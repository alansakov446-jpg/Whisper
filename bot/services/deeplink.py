from __future__ import annotations

from dataclasses import dataclass

from bot.constants import TOKEN_RE


@dataclass
class StartPayload:
    kind: str
    token: str | None = None
    group_id: int | None = None


def parse_start_payload(payload: str | None) -> StartPayload:
    raw = (payload or "").strip()
    if not raw:
        return StartPayload("none")
    if raw in {"help", "inline", "start"}:
        return StartPayload("help")
    if raw.startswith("w_"):
        token = raw[2:]
        if TOKEN_RE.match(token):
            return StartPayload("whisper", token=token)
        return StartPayload("unknown")
    if raw.startswith("ttl_"):
        token = raw[4:]
        if TOKEN_RE.match(token):
            return StartPayload("ttl", token=token)
        return StartPayload("unknown")
    if raw.startswith("grp_"):
        group_raw = raw[4:]
        if group_raw.isdigit():
            return StartPayload("group", group_id=int(group_raw))
        return StartPayload("unknown")
    return StartPayload("unknown")


def whisper_link(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start=w_{token}"


def group_link(bot_username: str, group_id: int) -> str:
    return f"https://t.me/{bot_username}?start=grp_{group_id}"


def ttl_link(bot_username: str, token: str) -> str:
    return f"https://t.me/{bot_username}?start=ttl_{token}"
