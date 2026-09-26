from __future__ import annotations

from aiohttp import web
from loguru import logger
from sqlalchemy import text


def build_health_app(session_factory) -> web.Application:
    app = web.Application()
    app["session_factory"] = session_factory
    app.router.add_get("/health", health)
    return app


async def health(request: web.Request) -> web.Response:
    factory = request.app["session_factory"]
    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("health check failed: {}", type(exc).__name__)
        return web.json_response({"status": "degraded", "db": "error"}, status=503)
    return web.json_response({"status": "ok", "db": "ok"})


async def start_health_server(session_factory, host: str, port: int):
    app = build_health_app(session_factory)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host=host, port=port)
    await site.start()
    return runner
