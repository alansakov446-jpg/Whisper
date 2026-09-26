from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.ext.asyncio import create_async_engine

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from bot.db.session import to_async_url  # noqa: E402

config = context.config


def _url() -> str:
    url = os.environ.get("WHISPER_MIGRATE_URL")
    if not url:
        raise RuntimeError("WHISPER_MIGRATE_URL is not set")
    return url


def run_migrations_offline() -> None:
    url, _connect_args = to_async_url(_url())
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def _do_run(connection) -> None:
    context.configure(connection=connection, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    url, connect_args = to_async_url(_url())
    engine = create_async_engine(url, poolclass=pool.NullPool, connect_args=connect_args)

    async def go() -> None:
        async with engine.connect() as connection:
            await connection.run_sync(_do_run)
        await engine.dispose()

    asyncio.run(go())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
