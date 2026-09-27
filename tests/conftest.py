from __future__ import annotations

from datetime import datetime

import pytest
from cryptography.fernet import Fernet
from loguru import logger
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.db.base import Base
from bot.db.models import User
from bot.services.crypto import Cipher
from bot.utils.time import utcnow

logger.remove()


@pytest.fixture
def cipher() -> Cipher:
    return Cipher(Fernet.generate_key().decode())


@pytest.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        yield db
    await engine.dispose()


@pytest.fixture
def session_factory(session):
    class _Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            if exc:
                await session.rollback()
            else:
                await session.commit()
            return False

    return _Factory()


async def make_user(session, user_id: int, username: str | None = None, **kwargs) -> User:
    now = kwargs.pop("now", None) or utcnow()
    user = User(
        id=user_id,
        username=username,
        first_name=kwargs.pop("first_name", None),
        last_name=kwargs.pop("last_name", None),
        lang=kwargs.pop("lang", "ru"),
        pseudonym=kwargs.pop("pseudonym", None),
        default_ttl=kwargs.pop("default_ttl", None),
        default_mode=kwargs.pop("default_mode", "open"),
        notifications=kwargs.pop("notifications", True),
        seal_default=kwargs.pop("seal_default", False),
        quiet_from=kwargs.pop("quiet_from", None),
        quiet_to=kwargs.pop("quiet_to", None),
        started_at=kwargs.pop("started_at", now),
        created_at=now,
    )
    session.add(user)
    await session.flush()
    return user


def dt(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
