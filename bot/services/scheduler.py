from __future__ import annotations

from datetime import timedelta

from loguru import logger

from bot.config import Settings
from bot.services.i18n import t
from bot.services.whisper_service import (
    cleanup_drafts,
    collect_refs,
    destroy_expired,
    take_due,
)
from bot.utils.time import utcnow


async def edit_destroyed_messages(bot, whisper, text: str | None = None) -> int:
    text = text or t(whisper.lang, "destroyed")
    edited = 0
    seen: set[tuple] = set()
    for ref in collect_refs(whisper):
        key = tuple(sorted(ref.items()))
        if key in seen:
            continue
        seen.add(key)
        try:
            await bot.edit_message_text(text, reply_markup=None, **ref)
            edited += 1
        except Exception as exc:
            logger.info("destroy edit failed: {}", type(exc).__name__)
    return edited


async def expire_job(session_factory, bot) -> int:
    async with session_factory() as session:
        destroyed = await destroy_expired(session, utcnow())
        await session.commit()
        snapshots = list(destroyed)
    edited = 0
    for whisper in snapshots:
        edited += await edit_destroyed_messages(bot, whisper)
    if snapshots:
        logger.info("destroyed {} whispers", len(snapshots))
    return edited


async def deliver_job(session_factory, bot, bot_username: str, deliver) -> int:
    async with session_factory() as session:
        due = await take_due(session, utcnow())
        await session.commit()
        tokens = [whisper.token for whisper in due]
    delivered = 0
    for token in tokens:
        async with session_factory() as session:
            from bot.db import repo

            whisper = await repo.get_whisper_by_token(session, token)
            if whisper is None:
                continue
            try:
                await deliver(bot, session, whisper, bot_username)
                whisper.status = "active"
                whisper.delivery_error = None
                whisper.updated_at = utcnow()
                delivered += 1
            except Exception as exc:
                whisper.delivery_attempts = (whisper.delivery_attempts or 0) + 1
                whisper.delivery_error = str(exc)[:200]
                whisper.updated_at = utcnow()
                whisper.status = "scheduled" if whisper.delivery_attempts < 10 else "failed"
                logger.warning("delivery failed for {}: {}", token, type(exc).__name__)
            await session.commit()
    return delivered


async def cleanup_job(session_factory) -> int:
    async with session_factory() as session:
        removed = await cleanup_drafts(session, utcnow())
        await session.commit()
    return removed


async def ping_db(session_factory) -> bool:
    from sqlalchemy import text

    async with session_factory() as session:
        await session.execute(text("SELECT 1"))
    return True


async def ping_heartbeat(url: str | None) -> bool:
    if not url:
        return False
    import aiohttp

    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(url) as response:
            return response.status < 500


def register_jobs(scheduler, session_factory, bot, settings: Settings, bot_username: str, deliver) -> None:
    scheduler.add_job(
        expire_job,
        "interval",
        minutes=1,
        args=[session_factory, bot],
        id="expire",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        deliver_job,
        "interval",
        seconds=30,
        args=[session_factory, bot, bot_username, deliver],
        id="deliver",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    if settings.backup_interval_minutes > 0:
        scheduler.add_job(
            run_backup_job,
            "interval",
            minutes=settings.backup_interval_minutes,
            args=[settings],
            id="backup",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    scheduler.add_job(
        cleanup_job,
        "interval",
        hours=1,
        args=[session_factory],
        id="cleanup",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        heartbeat_job,
        "interval",
        minutes=4,
        args=[session_factory, settings.heartbeat_url],
        id="ping",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )


async def run_backup_job(settings: Settings) -> str:
    from bot.services.backup import run_backup

    try:
        return run_backup(settings)
    except Exception as exc:
        logger.warning("backup failed: {}", exc)
        return "error"


async def heartbeat_job(session_factory, heartbeat_url: str) -> None:
    try:
        await ping_db(session_factory)
    except Exception as exc:
        logger.warning("db ping failed: {}", exc)
    try:
        await ping_heartbeat(heartbeat_url)
    except Exception as exc:
        logger.info("heartbeat failed: {}", type(exc).__name__)


def stuck_cutoff(now):
    return now - timedelta(minutes=2)
