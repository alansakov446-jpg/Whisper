from __future__ import annotations

from aiogram import BaseMiddleware
from aiogram.types import InlineQuery
from loguru import logger

from bot.db import repo
from bot.services.group_service import claim_invites
from bot.services.i18n import t


class DbMiddleware(BaseMiddleware):
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def __call__(self, handler, event, data):
        async with self.session_factory() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
            except Exception:
                await session.rollback()
                raise
            else:
                await session.commit()
                return result


class UserMiddleware(BaseMiddleware):
    async def __call__(self, handler, event, data):
        session = data["session"]
        tg_user = data.get("event_from_user") or getattr(event, "from_user", None)
        if tg_user is not None and getattr(tg_user, "id", None) and not getattr(tg_user, "is_bot", False):
            db_user = await repo.upsert_user(session, tg_user)
            data["db_user"] = db_user
            joined = []
            if not isinstance(event, InlineQuery):
                joined = await claim_invites(session, db_user)
            data["just_joined_groups"] = joined
            bot = data.get("bot")
            if joined and db_user.notifications and bot is not None:
                for group in joined:
                    try:
                        await bot.send_message(
                            db_user.id,
                            t(db_user.lang, "group_notify_added", name=group.name),
                        )
                    except Exception:
                        logger.info("failed to notify {} about group {}", db_user.id, group.id)
        else:
            data["db_user"] = None
            data["just_joined_groups"] = []
        return await handler(event, data)


def setup_middlewares(dp, session_factory) -> None:
    db_mw = DbMiddleware(session_factory)
    user_mw = UserMiddleware()
    for observer in (dp.message, dp.callback_query, dp.inline_query, dp.chosen_inline_result):
        observer.middleware(db_mw)
        observer.middleware(user_mw)
