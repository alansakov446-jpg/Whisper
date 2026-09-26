from __future__ import annotations

import os
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from bot.config import Settings, resolve_fernet_key, validate_settings
from bot.db.base import Base
from bot.db.session import to_async_url
from bot.health import build_health_app
from bot.migrate import run_migrations
from bot.services.backup import backup_skip_reason, postgres_dump_command, push_dump_to_repo
from bot.services.export_stats import export_user, user_stats
from bot.services.scheduler import edit_destroyed_messages, register_jobs
from bot.services.whisper_service import NewWhisper, create_whisper, destroy_whisper
from tests.conftest import make_user


def test_neon_url_normalization():
    url, args = to_async_url(
        "postgresql://user:p%40ss@ep-abc.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
    )
    assert url.startswith("postgresql+asyncpg://")
    assert "sslmode" not in url
    assert "channel_binding" not in url
    assert args["ssl"] is True
    assert args["statement_cache_size"] == 0
    from sqlalchemy.engine.url import make_url

    assert make_url(url).password == "p@ss"
    sqlite_url, sqlite_args = to_async_url("sqlite:///./data/whisper.db")
    assert sqlite_url.startswith("sqlite+aiosqlite://")
    assert sqlite_args == {}
    short, _ = to_async_url("postgres://u:p@localhost/db")
    assert short.startswith("postgresql+asyncpg://")


def test_settings_validation(tmp_path, monkeypatch):
    bad = Settings(bot_token="", database_url="sqlite:///x", health_port=1)
    errors = validate_settings(bad, github_actions=False)
    assert any("BOT_TOKEN" in item for item in errors)
    actions = validate_settings(
        Settings(bot_token="1:abc", database_url="sqlite:///x"),
        github_actions=True,
    )
    assert any("Postgres" in item for item in actions)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    settings = Settings(bot_token="", fernet_key="", fernet_key_path=str(tmp_path / "fernet.key"))
    key = resolve_fernet_key(settings, allow_generate=True)
    Fernet(key.encode())
    assert (tmp_path / "fernet.key").exists()
    again = resolve_fernet_key(settings, allow_generate=True)
    assert again == key


def test_backup_skip_and_git(tmp_path):
    skipped = backup_skip_reason(Settings(database_url="sqlite:///x", backup_repo_url=""))
    assert skipped == "skip-not-postgres"
    command = postgres_dump_command("postgresql://u:p@localhost/db", "out.sql")
    assert command[0] == "pg_dump"
    assert command[-1] == "postgresql://u:p@localhost/db"
    bare = tmp_path / "bare.git"
    bare.mkdir()
    import subprocess

    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    dump = tmp_path / "whisper_test.sql"
    dump.write_text("SELECT 1;\n", encoding="utf-8")
    push_dump_to_repo(str(bare), dump, tmp_path / "work")
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "clone", str(bare), str(checkout)], check=True, capture_output=True)
    assert (checkout / "whisper_test.sql").read_text(encoding="utf-8") == "SELECT 1;\n"


def test_migration_matches_models(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrated.db'}"
    run_migrations(url)
    migrated = create_engine(url)
    created = create_engine(f"sqlite:///{tmp_path / 'created.db'}")
    Base.metadata.create_all(created)
    insp_m = inspect(migrated)
    insp_c = inspect(created)
    assert set(insp_m.get_table_names()) >= set(insp_c.get_table_names())
    for table in insp_c.get_table_names():
        assert {col["name"] for col in insp_m.get_columns(table)} == {col["name"] for col in insp_c.get_columns(table)}


@pytest.mark.asyncio
async def test_health_ok():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = build_health_app(factory)
    from aiohttp.test_utils import TestClient, TestServer

    async with TestClient(TestServer(app)) as client:
        response = await client.get("/health")
        body = await response.json()
    assert response.status == 200
    assert body["status"] == "ok"
    await engine.dispose()


@pytest.mark.asyncio
async def test_export_stats_and_destroy_edit(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="export me",
            recipient_id=bob.id,
            recipient_username="bob",
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    whisper.inline_message_id = "inline-msg"
    from bot.services.whisper_service import record_open

    await record_open(session, whisper, bob.id, "bob")
    payload = await export_user(session, alice.id, cipher)
    assert payload["sent"][0]["text"] == "export me"
    assert payload["sent"][0]["access"] is True
    received = await export_user(session, bob.id, cipher)
    assert received["received"]
    stats = await user_stats(session, alice.id, cipher)
    assert stats["sent"] == 1
    assert stats["read"] == 1
    destroy_whisper(whisper, __import__("bot.utils.time", fromlist=["utcnow"]).utcnow())
    hidden = await export_user(session, alice.id, cipher)
    assert hidden["sent"][0]["text"] is None

    class Bot:
        def __init__(self):
            self.edits = []

        async def edit_message_text(self, text, reply_markup=None, **ref):
            self.edits.append((text, ref))

    bot = Bot()
    edited = await edit_destroyed_messages(bot, whisper)
    assert edited == 1
    assert bot.edits[0][0] == "💥 Шёпот самоуничтожен"


def test_scheduler_registers_jobs():
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    scheduler = AsyncIOScheduler(timezone="UTC")
    settings = Settings(bot_token="1:abc", database_url="sqlite:///x", backup_interval_minutes=30, heartbeat_url="")
    register_jobs(scheduler, object(), object(), settings, "Bot", object())
    ids = {job.id for job in scheduler.get_jobs()}
    assert {"expire", "deliver", "backup", "cleanup", "ping"} <= ids


def test_dump_command_does_not_use_shell():
    command = postgres_dump_command("postgresql://localhost/db", "a.sql")
    assert isinstance(command, list)
    assert "--file" in command
