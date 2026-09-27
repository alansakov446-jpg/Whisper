from aiogram import Router

from bot.handlers import convert, groups, inline, misc, send, settings, start, whispers


def build_router() -> Router:
    root = Router(name="root")
    root.include_router(misc.router)
    root.include_router(start.router)
    root.include_router(settings.router)
    root.include_router(groups.router)
    root.include_router(send.router)
    root.include_router(convert.router)
    root.include_router(whispers.router)
    root.include_router(inline.router)
    root.error()(misc.on_error)
    return root
