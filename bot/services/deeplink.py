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
    # grp_invite_ must be checked before grp_
    if raw.startswith("grp_invite_"):
        token = raw[len("grp_invite_") :]
        if TOKEN_RE.match(token):
            return StartPayload("group_invite", token=token)
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


def group_invite_link(bot_username: str, invite_token: str) -> str:
    return f"https://t.me/{bot_username}?start=grp_invite_{invite_token}"


def make_web_link(base_url: str, token: str, secret: str, now, ttl_sec: int = 300) -> str:
    """Self-checking preview link: base/w/TOKEN?exp=..&sig=.. valid `ttl_sec` seconds."""
    import hashlib
    import hmac

    from bot.utils.time import as_naive_utc

    base = (base_url or "").rstrip("/")
    current = as_naive_utc(now)
    exp = int(current.timestamp()) + int(ttl_sec)
    message = f"{token}:{exp}".encode()
    sig = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]
    return f"{base}/w/{token}?exp={exp}&sig={sig}"


def web_link_valid(base_url: str, token: str, secret: str, exp: int, sig: str, now) -> bool:
    import hashlib
    import hmac as hmac_mod

    from bot.utils.time import as_naive_utc

    current = as_naive_utc(now)
    if current is None or int(current.timestamp()) > int(exp):
        return False
    expected = hmac_mod.new(secret.encode(), f"{token}:{int(exp)}".encode(), hashlib.sha256).hexdigest()[:32]
    return hmac_mod.compare_digest(expected, sig or "")
