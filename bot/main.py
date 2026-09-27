from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import BotCommand
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from bot.config import Settings, get_settings, resolve_fernet_key, validate_settings
from bot.db.session import make_engine, make_session_factory
from bot.handlers import build_router
from bot.health import start_health_server
from bot.logging_setup import setup_logging
from bot.migrate import run_migrations
from bot.middlewares import setup_middlewares
from bot.services.crypto import Cipher
from bot.services.delivery import deliver_outgoing
from bot.services.i18n import t
from bot.services.scheduler import register_jobs


def commands_for(lang: str) -> list[BotCommand]:
    return [
        BotCommand(command="start", description=t(lang, "cmd_start")),
        BotCommand(command="send", description=t(lang, "cmd_send")),
        BotCommand(command="settings", description=t(lang, "cmd_settings")),
        BotCommand(command="cgn", description=t(lang, "cmd_cgn")),
        BotCommand(command="convert", description=t(lang, "cmd_convert")),
        BotCommand(command="myconvert", description=t(lang, "cmd_myconvert")),
        BotCommand(command="search", description=t(lang, "cmd_search")),
        BotCommand(command="pins", description=t(lang, "cmd_pins")),
        BotCommand(command="stats", description=t(lang, "cmd_stats")),
        BotCommand(command="export", description=t(lang, "cmd_export")),
        BotCommand(command="nomedia", description=t(lang, "cmd_nomedia")),
        BotCommand(command="cancel", description=t(lang, "cmd_cancel")),
    ]


def build_dispatcher(session_factory) -> Dispatcher:
    dp = Dispatcher(storage=MemoryStorage())
    setup_middlewares(dp, session_factory)
    dp.include_router(build_router())
    return dp


async def configure_bot(bot: Bot) -> str:
    me = await bot.get_me()
    username = me.username or "bot"
    try:
        await bot.set_my_commands(commands_for("ru"))
        await bot.set_my_commands(commands_for("ru"), language_code="ru")
        await bot.set_my_commands(commands_for("en"), language_code="en")
    except Exception as exc:
        logger.warning("set_my_commands failed: {}", exc)
    try:
        await bot.set_my_description(t("ru", "bot_description"))
        await bot.set_my_short_description(t("ru", "bot_short"))
    except Exception as exc:
        logger.warning("set description failed: {}", exc)
    return username


async def run_bot(settings: Settings, fernet_key: str) -> None:
    Path("data").mkdir(parents=True, exist_ok=True)
    engine = make_engine(settings.database_url)
    session_factory = make_session_factory(engine)
    cipher = Cipher(fernet_key)
    bot = Bot(settings.bot_token, default=DefaultBotProperties(protect_content=False))
    dp = build_dispatcher(session_factory)
    dp.workflow_data["cipher"] = cipher
    dp.workflow_data["web_base_url"] = settings.web_base_url
    dp.workflow_data["session_factory"] = session_factory
    username = "bot"
    runner = None
    scheduler = AsyncIOScheduler(timezone="UTC")
    try:
        username = await configure_bot(bot)
        dp.workflow_data["bot_username"] = username
        register_jobs(scheduler, session_factory, bot, settings, username, deliver_outgoing)
        scheduler.start()
        try:
            runner = await start_health_server(session_factory, settings.health_host, settings.health_port)
            logger.info("health server on {}:{}", settings.health_host, settings.health_port)
        except OSError as exc:
            logger.error("health server did not start: {}", exc)
        logger.info("whisper bot @{} is polling", username)
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)
        if runner is not None:
            await runner.cleanup()
        await engine.dispose()


def main() -> None:
    settings = get_settings()
    errors = validate_settings(settings)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        raise SystemExit(1)
    try:
        key = resolve_fernet_key(settings)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1) from exc
    setup_logging(settings)
    if not settings.fernet_key:
        logger.warning("FERNET_KEY was empty — using a local key. Set FERNET_KEY before production.")
    run_migrations(settings.database_url)
    asyncio.run(run_bot(settings, key))


if __name__ == "__main__":
    main()
