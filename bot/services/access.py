from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from bot.services.crypto import Cipher, resolve_sender_id
from bot.utils.time import as_naive_utc


@dataclass
class AccessContext:
    user_id: int
    username: str | None = None
    is_group_member: bool = False
    is_invited: bool = False


def is_sender(whisper, user_id: int, cipher: Cipher) -> bool:
    if whisper.sender_id is not None and int(whisper.sender_id) == int(user_id):
        return True
    if cipher.hmac_matches(user_id, whisper.sender_hmac):
        return True
    if whisper.sender_id_enc:
        try:
            return cipher.decrypt_sender(whisper.sender_id_enc) == int(user_id)
        except ValueError:
            return False
    return False


def is_recipient(whisper, ctx: AccessContext) -> bool:
    if whisper.recipient_id is not None and int(whisper.recipient_id) == int(ctx.user_id):
        return True
    if whisper.recipient_username and ctx.username:
        return whisper.recipient_username.lower() == ctx.username.lower().lstrip("@")
    return False


def check_access(whisper, ctx: AccessContext, cipher: Cipher, now: datetime) -> str:
    """Return allow, deny, destroyed, not_yet, or rejected.

    Group whispers are readable only by current members (and pending invitees).
    Leaving or being removed drops access to old and new group whispers, even
    for the original sender. A direct whisper stays readable by sender and recipient.
    """
    expires = as_naive_utc(whisper.expires_at)
    now_n = as_naive_utc(now)
    destroyed = whisper.status == "destroyed" or (expires is not None and now_n is not None and expires <= now_n)
    if destroyed or whisper.status == "destroyed":
        return "destroyed"
    if whisper.status == "rejected":
        return "rejected"
    if whisper.is_group:
        if ctx.is_group_member or ctx.is_invited:
            if whisper.status == "scheduled" and as_naive_utc(whisper.send_at) and as_naive_utc(whisper.send_at) > now_n:
                if is_sender(whisper, ctx.user_id, cipher):
                    return "allow"
                return "not_yet"
            return "allow"
        return "deny"
    sender = is_sender(whisper, ctx.user_id, cipher)
    recipient = is_recipient(whisper, ctx)
    if not sender and not recipient:
        return "deny"
    if (
        whisper.status == "scheduled"
        and as_naive_utc(whisper.send_at)
        and now_n
        and as_naive_utc(whisper.send_at) > now_n
        and not sender
    ):
        return "not_yet"
    return "allow"


def sender_id_of(whisper, cipher: Cipher) -> int | None:
    return resolve_sender_id(whisper, cipher)
