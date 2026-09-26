from __future__ import annotations

from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def to_async_url(url: str) -> tuple[str, dict]:
    """Return (sqlalchemy async url, connect_args).

    Neon URLs often include ``sslmode`` and ``channel_binding``, which asyncpg
    does not accept as query parameters. SSL is passed via connect_args.
    """
    raw = (url or "").strip()
    if raw.startswith("sqlite"):
        if raw.startswith("sqlite://") and "+aiosqlite" not in raw:
            raw = "sqlite+aiosqlite://" + raw[len("sqlite://") :]
        return raw, {}

    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://") :]
    for driver in ("postgresql+asyncpg://", "postgresql+psycopg://", "postgresql+psycopg2://"):
        if raw.startswith(driver):
            raw = "postgresql://" + raw[len(driver) :]
            break

    parsed = make_url(raw)
    query = dict(parsed.query)
    sslmode = str(query.pop("sslmode", "") or "")
    query.pop("channel_binding", None)
    host = parsed.host or ""
    connect_args: dict = {
        "prepared_statement_cache_size": 0,
        "statement_cache_size": 0,
    }
    if sslmode.lower() in {"require", "verify-ca", "verify-full", "prefer"} or "neon.tech" in host:
        connect_args["ssl"] = True
    elif sslmode.lower() == "disable":
        connect_args["ssl"] = False
    parsed = parsed.set(drivername="postgresql+asyncpg", query=query)
    return parsed.render_as_string(hide_password=False), connect_args


def make_engine(url: str) -> AsyncEngine:
    async_url, connect_args = to_async_url(url)
    if async_url.startswith("sqlite"):
        return create_async_engine(
            async_url,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
    return create_async_engine(
        async_url,
        pool_pre_ping=True,
        pool_size=2,
        max_overflow=2,
        pool_recycle=300,
        connect_args=connect_args,
    )


def make_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=True)
