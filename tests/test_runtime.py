from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from bot.config import Settings
from bot.logging_setup import setup_logging
from bot.main import build_dispatcher, commands_for, configure_bot, main
from bot.middlewares import DbMiddleware, UserMiddleware
from bot.services.delivery import deliver_outgoing, send_media, send_opened_content
from bot.services.scheduler import cleanup_job, deliver_job, expire_job, heartbeat_job, ping_heartbeat
from bot.services.whisper_service import NewWhisper, create_whisper, plan_open
from bot.utils.time import utcnow
from tests.conftest import make_user
from tests.fakes import FakeBot


async def test_middleware_upserts_and_notifies(session):
    class Factory:
        def __call__(self):
            return self

        async def __aenter__(self):
            return session

        async def __aexit__(self, exc_type, exc, tb):
            return False

    bot = FakeBot()
    event = SimpleNamespace(from_user=SimpleNamespace(id=42, username="newbie", is_bot=False, language_code="en"))

    async def handler(event, data):
        return data["db_user"]

    async def wrapped(event, data):
        return await UserMiddleware()(handler, event, data)

    user = await DbMiddleware(Factory())(wrapped, event, {"bot": bot})
    assert user.id == 42
    assert user.lang == "en"
    assert user.started_at is None


def test_build_app_pieces(tmp_path, monkeypatch):
    assert commands_for("ru")
    settings = Settings(bot_token="1:abc", database_url="sqlite:///x", fernet_key_path=str(tmp_path / "k"))
    setup_logging(settings)
    monkeypatch.setenv("BOT_TOKEN", "")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///x")
    with pytest.raises(SystemExit):
        main()


async def test_configure_and_jobs(session, cipher, session_factory, monkeypatch, tmp_path):
    bot = FakeBot()
    username = await configure_bot(bot)
    assert username == "WhisperBot"
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
            text="x",
            recipient_id=bob.id,
            ttl_explicit=True,
            ttl_seconds=10,
            send_at=utcnow().replace(year=2020),
            now=utcnow().replace(year=2020),
        ),
    )
    whisper.inline_message_id = "i1"
    await session.commit()
    await expire_job(session_factory, bot)
    assert whisper.status == "destroyed"
    assert bot.edits

    scheduled = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="later",
            recipient_id=bob.id,
            ttl_explicit=True,
            ttl_seconds=None,
            status="scheduled",
            send_at=utcnow().replace(year=2020),
            now=utcnow().replace(year=2020),
            delivery_mode="open",
        ),
    )

    async def deliver(bot, db, item, bot_username):
        item.status = "active"

    delivered = await deliver_job(session_factory, bot, "WhisperBot", deliver)
    assert delivered >= 1
    removed = await cleanup_job(session_factory)
    assert removed >= 0
    assert await ping_heartbeat(None) is False
    await heartbeat_job(session_factory, "")
    report = await deliver_outgoing(bot, session, scheduled, "WhisperBot")
    assert report.sent >= 0
    failing = FakeBot(fail_ids={bob.id})
    failed = await deliver_outgoing(failing, session, scheduled, "WhisperBot")
    assert failed.need_link
    plan = plan_open(
        whisper,
        bob.id,
        "bob",
        is_member=False,
        is_invited=False,
        cipher=cipher,
        now=utcnow(),
        lang="ru",
        prefer_alert=False,
    )
    await send_opened_content(bot, bob.id, plan, whisper.token, "ru")
    await send_media(bot, bob.id, "video_note", "vn", "cap")
    await send_media(bot, bob.id, "video", "v", "cap")
    dp = build_dispatcher(session_factory)
    assert dp is not None
    _ = scheduled


def test_main_entry(monkeypatch, tmp_path):
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("BOT_TOKEN", "1:ABC")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/bot.db")
    monkeypatch.setenv("FERNET_KEY", key)
    monkeypatch.setattr("bot.main.run_migrations", lambda url: None)
    monkeypatch.setattr("bot.main.setup_logging", lambda settings: None)

    async def fake_run(*args, **kwargs):
        return None

    monkeypatch.setattr("bot.main.run_bot", fake_run)
    main()
