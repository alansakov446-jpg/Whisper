from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from bot.constants import (
    ALERT_MAX,
    CAPTION_LIMIT,
    DRAFT_TTL_HOURS,
    PAGE_SIZE,
    RATE_LIMIT_COUNT,
    RATE_LIMIT_WINDOW_SEC,
    TOKEN_BYTES,
)
from bot.db import repo
from bot.db.models import Whisper
from bot.services.access import AccessContext, check_access, is_sender
from bot.services.crypto import Cipher, resolve_sender_id
from bot.services.i18n import format_ttl, t
from bot.services.ttl import compute_expires, resolve_ttl
from bot.utils.time import as_naive_utc, utcnow


@dataclass
class NewWhisper:
    sender_id: int
    sender_username: str | None
    pseudonym: str | None
    lang: str
    text: str | None = None
    recipient_id: int | None = None
    recipient_username: str | None = None
    group_id: int | None = None
    group_name: str | None = None
    is_group: bool = False
    media_type: str | None = None
    media_file_id: str | None = None
    ttl_seconds: int | None = None
    ttl_explicit: bool = False
    default_ttl: int | None = None
    delivery_mode: str = "inline"
    is_anonymous: bool = False
    send_at: datetime | None = None
    status: str = "active"
    pending_targets: dict | None = None
    token: str | None = None
    now: datetime | None = None


@dataclass
class ActivateResult:
    status: str
    whisper: Whisper | None = None
    new_member_ids: list[int] = field(default_factory=list)
    group_name: str | None = None


def new_token() -> str:
    return secrets.token_urlsafe(TOKEN_BYTES)


async def is_rate_limited(session: AsyncSession, sender_hmac: str, now: datetime) -> bool:
    count = await repo.count_recent_sends(session, sender_hmac, now, RATE_LIMIT_WINDOW_SEC)
    return count >= RATE_LIMIT_COUNT


async def create_whisper(session: AsyncSession, cipher: Cipher, data: NewWhisper) -> Whisper:
    now = as_naive_utc(data.now) or utcnow()
    send_at = as_naive_utc(data.send_at) or now
    token = data.token or new_token()
    anonymous = bool(data.is_anonymous)
    sender_hmac = cipher.hmac_sender(data.sender_id)
    sender_id = None if anonymous else data.sender_id
    sender_enc = cipher.encrypt_sender(data.sender_id) if anonymous else None
    if data.status == "draft":
        ttl_seconds = data.ttl_seconds
        expires_at = None
    else:
        ttl_seconds = resolve_ttl(
            explicit=data.ttl_explicit,
            chosen=data.ttl_seconds,
            default=data.default_ttl,
        )
        expires_at = compute_expires(send_at, ttl_seconds)
    whisper = Whisper(
        token=token,
        sender_id=sender_id,
        sender_id_enc=sender_enc,
        sender_hmac=sender_hmac,
        recipient_id=data.recipient_id,
        recipient_username=data.recipient_username,
        group_id=data.group_id,
        group_name=data.group_name,
        text=data.text,
        media_type=data.media_type,
        media_file_id=data.media_file_id,
        ttl_seconds=ttl_seconds,
        ttl_explicit=data.ttl_explicit or data.status != "draft",
        expires_at=expires_at,
        send_at=send_at,
        delivery_mode=data.delivery_mode,
        is_anonymous=anonymous,
        is_group=data.is_group,
        status=data.status,
        lang=data.lang or "ru",
        pending_targets=json.dumps(data.pending_targets) if data.pending_targets else None,
        pseudonym_snapshot=data.pseudonym,
        sender_username=data.sender_username,
        created_at=now,
        updated_at=now,
    )
    session.add(whisper)
    await session.flush()
    return whisper


async def remember_contact(
    session: AsyncSession,
    owner_id: int,
    username: str | None,
    contact_user_id: int | None,
    now: datetime,
) -> None:
    await repo.upsert_contact(session, owner_id, username, contact_user_id, now)


async def activate_draft(
    session: AsyncSession,
    token: str,
    inline_message_id: str | None,
    cipher: Cipher,
    now: datetime | None = None,
) -> ActivateResult:
    from bot.services.group_service import create_group

    now = as_naive_utc(now) or utcnow()
    whisper = await repo.get_whisper_by_token(session, token)
    if whisper is None:
        return ActivateResult("missing")
    if inline_message_id and not whisper.inline_message_id:
        add_delivery_ref(whisper, inline_message_id=inline_message_id)
    if whisper.status == "active":
        return ActivateResult("active", whisper)
    if whisper.status == "destroyed":
        return ActivateResult("destroyed", whisper)
    if whisper.status == "rejected":
        return ActivateResult("rejected", whisper)
    if whisper.status != "draft":
        return ActivateResult(whisper.status, whisper)
    sender_id = resolve_sender_id(whisper, cipher)
    if sender_id is None or not whisper.sender_hmac:
        return ActivateResult("missing", whisper)
    if await is_rate_limited(session, whisper.sender_hmac, now):
        whisper.status = "rejected"
        whisper.text = None
        whisper.media_file_id = None
        whisper.media_type = None
        whisper.updated_at = now
        return ActivateResult("limited", whisper)

    new_member_ids: list[int] = []
    group_name = whisper.group_name
    pending = None
    if whisper.pending_targets:
        try:
            pending = json.loads(whisper.pending_targets)
        except json.JSONDecodeError:
            pending = None
    if pending and (pending.get("usernames") or pending.get("user_ids")):
        created = await create_group(
            session,
            name=pending.get("name") or "Whisper",
            creator_id=sender_id,
            creator_username=whisper.sender_username,
            member_user_ids=list(pending.get("user_ids") or []),
            invite_usernames=list(pending.get("usernames") or []),
            now=now,
        )
        whisper.group_id = created.group.id
        whisper.group_name = created.group.name
        whisper.is_group = True
        whisper.pending_targets = None
        group_name = created.group.name
        new_member_ids = [member.user_id for member in created.members if member.user_id != sender_id]
        for username in pending.get("usernames") or []:
            await remember_contact(session, sender_id, username, None, now)
        for uid in pending.get("user_ids") or []:
            user = await repo.get_user(session, uid)
            await remember_contact(session, sender_id, user.username if user else None, uid, now)

    if not whisper.ttl_explicit:
        sender = await repo.get_user(session, sender_id)
        whisper.ttl_seconds = resolve_ttl(
            explicit=False,
            chosen=None,
            default=sender.default_ttl if sender else None,
        )
    whisper.created_at = now
    whisper.send_at = now
    whisper.expires_at = compute_expires(now, whisper.ttl_seconds)
    whisper.status = "active"
    whisper.updated_at = now
    if whisper.recipient_username or whisper.recipient_id:
        await remember_contact(session, sender_id, whisper.recipient_username, whisper.recipient_id, now)
    if whisper.media_file_id:
        await repo.clear_pending_media(session, sender_id)
    return ActivateResult("activated", whisper, new_member_ids=new_member_ids, group_name=group_name)


def add_delivery_ref(whisper: Whisper, **ref) -> None:
    clean = {key: value for key, value in ref.items() if value is not None}
    if not clean:
        return
    refs: list[dict] = []
    if whisper.delivery_refs:
        try:
            loaded = json.loads(whisper.delivery_refs)
            if isinstance(loaded, list):
                refs = loaded
        except json.JSONDecodeError:
            refs = []
    refs.append(clean)
    whisper.delivery_refs = json.dumps(refs)
    if clean.get("inline_message_id"):
        whisper.inline_message_id = clean["inline_message_id"]
    if clean.get("chat_id") is not None and clean.get("message_id") is not None:
        whisper.chat_id = clean["chat_id"]
        whisper.message_id = clean["message_id"]


def collect_refs(whisper: Whisper) -> list[dict]:
    refs: list[dict] = []
    if whisper.delivery_refs:
        try:
            loaded = json.loads(whisper.delivery_refs)
            if isinstance(loaded, list):
                refs.extend(item for item in loaded if isinstance(item, dict))
        except json.JSONDecodeError:
            pass
    if whisper.inline_message_id:
        refs.append({"inline_message_id": whisper.inline_message_id})
    if whisper.chat_id and whisper.message_id:
        refs.append({"chat_id": whisper.chat_id, "message_id": whisper.message_id})
    return refs


async def destroy_expired(session: AsyncSession, now: datetime | None = None) -> list[Whisper]:
    now = as_naive_utc(now) or utcnow()
    rows = await repo.list_expired(session, now)
    for whisper in rows:
        destroy_whisper(whisper, now)
    await session.flush()
    return rows


def destroy_whisper(whisper: Whisper, now: datetime) -> None:
    whisper.text = None
    whisper.media_file_id = None
    whisper.media_type = None
    whisper.status = "destroyed"
    whisper.destroyed_at = now
    whisper.updated_at = now


async def change_ttl(
    session: AsyncSession,
    whisper: Whisper,
    actor_id: int,
    ttl_seconds: int | None,
    cipher: Cipher,
    now: datetime | None = None,
) -> str:
    now = as_naive_utc(now) or utcnow()
    if whisper.status == "destroyed":
        return "destroyed"
    if not is_sender(whisper, actor_id, cipher):
        return "denied"
    whisper.ttl_seconds = ttl_seconds
    whisper.ttl_explicit = True
    base = as_naive_utc(whisper.send_at) or as_naive_utc(whisper.created_at) or now
    whisper.expires_at = compute_expires(base, ttl_seconds)
    whisper.updated_at = now
    if whisper.expires_at is not None and whisper.expires_at <= now and whisper.status == "active":
        destroy_whisper(whisper, now)
        return "destroyed_now"
    return "ok"


async def record_open(
    session: AsyncSession,
    whisper: Whisper,
    user_id: int,
    username: str | None,
    now: datetime | None = None,
) -> None:
    now = as_naive_utc(now) or utcnow()
    if whisper.recipient_id is None and username and whisper.recipient_username:
        if whisper.recipient_username.lower() == username.lower().lstrip("@"):
            whisper.recipient_id = user_id
    if whisper.read_at is None:
        whisper.read_at = now
    existing = await repo.get_receipt(session, whisper.id, user_id)
    if existing is None:
        from bot.db.models import ReadReceipt

        session.add(ReadReceipt(whisper_id=whisper.id, user_id=user_id, read_at=now))
    await session.flush()


async def cleanup_drafts(session: AsyncSession, now: datetime | None = None, hours: int = DRAFT_TTL_HOURS) -> int:
    now = as_naive_utc(now) or utcnow()
    return await repo.delete_old_drafts(session, now - timedelta(hours=hours))


async def take_due(session: AsyncSession, now: datetime | None = None) -> list[Whisper]:
    now = as_naive_utc(now) or utcnow()
    cutoff = now - timedelta(minutes=2)
    for stuck in await repo.list_stuck_delivering(session, cutoff):
        stuck.status = "scheduled"
        stuck.updated_at = now
    due = await repo.list_due_scheduled(session, now)
    for whisper in due:
        whisper.status = "delivering"
        whisper.updated_at = now
    await session.flush()
    return due


@dataclass
class OpenPlan:
    status: str
    message: str
    page_text: str | None = None
    page: int = 0
    pages: int = 1
    use_alert: bool = False
    media_type: str | None = None
    media_file_id: str | None = None
    header: str = ""
    body: str = ""
    should_destroy: bool = False


def content_header(whisper: Whisper, viewer_id: int, cipher: Cipher, lang: str) -> str:
    sender = is_sender(whisper, viewer_id, cipher)
    if whisper.is_anonymous and not sender:
        return t(lang, "opened_anon")
    if whisper.is_anonymous and sender:
        return t(lang, "opened_yours_anon")
    name = whisper.pseudonym_snapshot
    if not name and whisper.sender_username:
        name = f"@{whisper.sender_username}"
    if name and not sender:
        return t(lang, "opened_from", name=name)
    return t(lang, "opened_header")


def paginate(header: str, body: str, page: int, page_size: int = PAGE_SIZE) -> tuple[str, int, int]:
    body = body or ""
    size = max(500, page_size - len(header) - 2)
    pages = [body[i : i + size] for i in range(0, len(body), size)] or [""]
    page = max(0, min(page, len(pages) - 1))
    text = f"{header}\n\n{pages[page]}" if header else pages[page]
    return text, page, len(pages)


def split_caption(header: str, body: str | None, limit: int = CAPTION_LIMIT) -> tuple[str, str | None]:
    body = body or ""
    combined = header + ("\n\n" + body if body else "")
    if len(combined) <= limit:
        return combined, None
    if len(header) <= limit:
        return header, body or None
    return header[:limit], body or None


def plan_open(
    whisper: Whisper,
    viewer_id: int,
    viewer_username: str | None,
    *,
    is_member: bool,
    is_invited: bool,
    cipher: Cipher,
    now: datetime,
    lang: str,
    page: int = 0,
    prefer_alert: bool = True,
) -> OpenPlan:
    ctx = AccessContext(
        user_id=viewer_id,
        username=viewer_username,
        is_group_member=is_member,
        is_invited=is_invited,
    )
    access = check_access(whisper, ctx, cipher, now)
    should_destroy = access == "destroyed" and whisper.status != "destroyed"
    if access == "destroyed":
        return OpenPlan(status="destroyed", message=t(lang, "destroyed"), should_destroy=should_destroy)
    if access == "rejected":
        return OpenPlan(status="rejected", message=t(lang, "rejected_open"))
    if access == "not_yet":
        return OpenPlan(status="not_yet", message=t(lang, "not_yet"))
    if access == "deny":
        return OpenPlan(status="deny", message=t(lang, "not_yours"))
    header = content_header(whisper, viewer_id, cipher, lang)
    body = whisper.text or ""
    page_text, page, pages = paginate(header, body, page)
    use_alert = False
    message = page_text
    if prefer_alert and not whisper.media_file_id and pages == 1:
        candidate = body if len(header) + 1 + len(body) > ALERT_MAX else (f"{header}\n{body}" if body else header)
        if candidate and len(candidate) <= ALERT_MAX:
            use_alert = True
            message = candidate
        elif body and len(body) <= ALERT_MAX:
            use_alert = True
            message = body
    return OpenPlan(
        status="ok",
        message=message,
        page_text=page_text,
        page=page,
        pages=pages,
        use_alert=use_alert,
        media_type=whisper.media_type,
        media_file_id=whisper.media_file_id,
        header=header,
        body=body,
    )


def ttl_label(seconds: int | None, lang: str) -> str:
    return format_ttl(seconds, lang)
