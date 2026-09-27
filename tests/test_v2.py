from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from bot.constants import ENVELOPE_LABELS, MEDIA_KEY_ALPHABET
from bot.db import repo
from bot.services import convert
from bot.services.crypto import Cipher
from bot.services.deeplink import group_invite_link, parse_start_payload, web_link_valid
from bot.services.deeplink import make_web_link
from bot.services.inline_plan import build_inline_plan
from bot.services.parser import (
    parse_inline_query,
    parse_media_key,
    parse_recipients,
    parse_sealed,
    parse_text_without_meta,
)
from bot.services.seal import new_seal_key, seal_decrypt, seal_encrypt
from bot.services.whisper_service import (
    NewWhisper,
    create_whisper,
    delete_by_sender,
    extend_ttl,
    plan_open,
)
from bot.utils.time import in_quiet_hours, parse_hhmm, utcnow
from tests.conftest import make_user
from tests.fakes import FakeBot, FakeCallback, FakeChosen, FakeMessage, FakeState


# --- parser v2 --------------------------------------------------------------


def test_parse_recipients_inline_id_only():
    refs = parse_recipients("текст id=12345 id=67890 @bobuser 99999 id=12345")
    assert [(r.kind, r.value) for r in refs] == [("id", 12345), ("id", 67890), ("username", "bobuser")]
    bare = parse_inline_query("сумма 12345678 рублей")
    assert bare.text == "сумма 12345678 рублей"
    assert bare.user_ids == []
    many = parse_recipients(" ".join(f"id={50000 + i}" for i in range(60)))
    assert len(many) == 50


def test_parse_media_key_and_text_without_meta():
    assert parse_media_key('смотри MEDIA_ID="ABC234" тут') == "ABC234"
    assert parse_media_key("MEDIA_ID=XYZ789") == "XYZ789"
    assert parse_media_key("нет ключа") is None
    assert parse_media_key('MEDIA_ID="0O1I11"') is None  # bad alphabet
    text = parse_text_without_meta('привет @bobuser id=123456 MEDIA_ID="ABC234" e2e метка:💌 ttl:1h')
    assert text == "привет"
    assert parse_sealed("хочу e2e шёпот") is True
    assert parse_sealed("слово sweep здесь") is False


# --- inline plan v2 ---------------------------------------------------------


def test_inline_unknown_id_card_without_draft():
    parsed = parse_inline_query("секрет id=99999995")
    plan = build_inline_plan(
        parsed,
        lang="ru",
        activated=True,
        rate_limited=False,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym=None,
        default_ttl=None,
        saved_usernames=set(),
        saved_ids=set(),
        contacts=[],
        groups=[],
        id_users={99999995: None},
    )
    assert plan.articles[0].title == "⚠️ Пользователь id=99999995 не найден"
    assert all(article.draft is None for article in plan.articles)


def test_inline_multiple_ids_three_cards():
    parsed = parse_inline_query("секрет id=11111 id=22222 id=33333")

    class U:
        def __init__(self, uid, name):
            self.id = uid
            self.username = name
            self.first_name = None
            self.last_name = None

    plan = build_inline_plan(
        parsed,
        lang="ru",
        activated=True,
        rate_limited=False,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym=None,
        default_ttl=None,
        saved_usernames=set(),
        saved_ids=set(),
        contacts=[],
        groups=[],
        id_users={11111: U(11111, "one"), 22222: U(22222, "two"), 33333: U(33333, "three")},
    )
    titles = [article.title for article in plan.articles if article.draft]
    assert titles[0] == "🔒 Отправить шёпот группе (4 участников)"
    assert "участника" not in titles[0]
    assert titles[1] == "🔒 Отправить шёпот — только последний"
    assert titles[2] == "🔒 Отправить шёпот — только первый"
    group_draft = plan.articles[0].draft
    assert group_draft.new_group_ids == [11111, 22222, 33333]
    assert plan.articles[1].draft.recipient_id == 33333
    assert plan.articles[2].draft.recipient_id == 11111


def test_inline_no_recipient_warning():
    parsed = parse_inline_query("просто текст без получателя")
    plan = build_inline_plan(
        parsed,
        lang="ru",
        activated=True,
        rate_limited=False,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym=None,
        default_ttl=None,
        saved_usernames=set(),
        saved_ids=set(),
        contacts=[("oldfriend", 7)],
        groups=[],
    )
    assert plan.articles[0].title == "⚠️ Не указан получатель. Укажите @username или id=ЧИСЛО"


# --- media keys -------------------------------------------------------------


async def test_media_key_alphabet_and_owner(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    result = await convert.create_key(
        session, owner_id=alice.id, files=[{"type": "photo", "file_id": "F1"}], now=utcnow()
    )
    assert result.status == "ok"
    assert len(result.key) == 6
    assert all(ch in MEDIA_KEY_ALPHABET for ch in result.key)
    assert "0" not in result.key and "1" not in result.key and "I" not in result.key and "O" not in result.key
    assert await convert.check_key(session, result.key, alice.id) == "ok"
    assert await convert.check_key(session, result.key, bob.id) == "not_yours"
    assert await convert.check_key(session, "ABC234", alice.id) == "missing"
    assert await convert.resolve_key(session, result.key, alice.id) == "ok"


async def test_media_key_album_and_rate_limit(session):
    alice = await make_user(session, 1, "alice")
    now = utcnow()
    first = await convert.create_key(
        session,
        owner_id=alice.id,
        files=[{"type": "photo", "file_id": "A"}],
        media_group="1:g1",
        now=now,
    )
    assert first.status == "ok"
    again = await convert.create_key(
        session,
        owner_id=alice.id,
        files=[{"type": "photo", "file_id": "B"}],
        media_group="1:g1",
        now=now,
    )
    assert again.status == "exists"
    assert again.key == first.key
    await convert.append_files(session, again.row, [{"type": "photo", "file_id": "B"}])
    files = await convert.key_files(session, first.key, now)
    assert [item["file_id"] for item in files] == ["A", "B"]
    for index in range(19):
        await convert.create_key(
            session, owner_id=alice.id, files=[{"type": "photo", "file_id": str(index)}], now=now
        )
    limited = await convert.create_key(
        session, owner_id=alice.id, files=[{"type": "photo", "file_id": "Z"}], now=now
    )
    assert limited.status == "rate_limited"


async def test_media_key_expiry_and_delete(session):
    alice = await make_user(session, 1, "alice")
    now = utcnow()
    result = await convert.create_key(session, owner_id=alice.id, files=[{"type": "photo", "file_id": "F"}], now=now)
    assert result.row.expires_at == now + timedelta(days=30)
    stale = now + timedelta(days=31)
    assert await convert.check_key(session, result.key, alice.id, stale) == "missing"
    assert await convert.key_files(session, result.key, stale) == []
    assert await repo.delete_media_key(session, alice.id, result.key) is True
    assert await convert.check_key(session, result.key, alice.id) == "missing"


# --- seal -------------------------------------------------------------------


def test_seal_roundtrip():
    key = new_seal_key()
    sealed = seal_encrypt("секретный текст", key)
    assert "секретный" not in sealed
    assert seal_decrypt(sealed, key) == "секретный текст"
    assert seal_decrypt(sealed, "wrong-key") is None


async def test_sealed_whisper_open_flow(session, cipher):
    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    eve = await make_user(session, 3, "eve")
    key = new_seal_key()
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text=seal_encrypt("запечатанный секрет", key),
            recipient_id=bob.id,
            recipient_username="bob",
            is_sealed=True,
            seal_key_enc=cipher.encrypt_secret(key),
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    sealed_plan = plan_open(
        whisper, bob.id, "bob", is_member=False, is_invited=False, cipher=cipher, now=utcnow(), lang="ru"
    )
    assert sealed_plan.status == "sealed"
    assert "запечатанный" not in sealed_plan.message
    bad = plan_open(
        whisper, bob.id, "bob", is_member=False, is_invited=False, cipher=cipher, now=utcnow(), lang="ru", seal_key="zzz"
    )
    assert bad.status == "sealed_bad_key"
    good = plan_open(
        whisper, bob.id, "bob", is_member=False, is_invited=False, cipher=cipher, now=utcnow(), lang="ru", seal_key=key
    )
    assert good.status == "ok"
    assert good.body == "запечатанный секрет"
    denied = plan_open(
        whisper, eve.id, "eve", is_member=False, is_invited=False, cipher=cipher, now=utcnow(), lang="ru"
    )
    assert denied.status == "deny"  # strangers never get the key prompt


async def test_seal_key_shown_once_on_chosen(session, cipher):
    from bot.handlers.inline import on_chosen, on_inline, persist_drafts
    from bot.services.inline_plan import build_inline_plan

    alice = await make_user(session, 1, "alice")
    query = SimpleNamespace(
        query="e2e секрет @bobuser",
        from_user=SimpleNamespace(id=1, username="alice", is_bot=False),
    )

    class Q:
        def __init__(self, text):
            self.query = text
            self.results = None
            self.kwargs = None

        async def answer(self, results, **kwargs):
            self.results = results
            self.kwargs = kwargs

    q = Q("e2e секрет для боба @bobuser")
    await on_inline(q, session, alice, cipher, "WhisperBot")
    assert q.results
    token = q.results[0].id
    whisper = await repo.get_whisper_by_token(session, token)
    assert whisper.is_sealed
    assert whisper.seal_key_enc
    assert "секрет" not in (whisper.text or "")
    bot = FakeBot()
    chosen = FakeChosen(token, "inline-sealed")
    await on_chosen(chosen, session, alice, cipher, bot)
    whisper = await repo.get_whisper_by_token(session, token)
    assert whisper.seal_key_enc is None  # erased after showing
    assert any("Ключ запечатывания" in text for _chat, text in bot.sent)
    _ = build_inline_plan
    _ = persist_drafts


# --- quiet hours ------------------------------------------------------------


def test_quiet_hours_helpers():
    assert parse_hhmm("23:30") == 23 * 60 + 30
    assert parse_hhmm("25:00") is None
    assert parse_hhmm(None) is None
    from tests.conftest import dt

    night = dt("2026-04-01 23:30:00")
    day = dt("2026-04-01 12:00:00")
    assert in_quiet_hours(night, "23:00", "07:00") is True  # wraps midnight
    assert in_quiet_hours(day, "23:00", "07:00") is False
    assert in_quiet_hours(day, "09:00", "18:00") is True
    assert in_quiet_hours(dt("2026-04-01 08:00:00"), "09:00", "18:00") is False
    assert in_quiet_hours(day, None, None) is False


async def test_group_reply_skips_quiet_members(session, cipher):
    from bot.services.delivery import deliver_outgoing

    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    carol = await make_user(session, 3, "carol")
    carol.quiet_from = "00:00"
    carol.quiet_to = "23:59"
    from bot.services.group_service import create_group

    created = await create_group(
        session,
        name="Отряд",
        creator_id=alice.id,
        creator_username="alice",
        member_user_ids=[bob.id, carol.id],
        now=utcnow(),
    )
    reply = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="ответ группе",
            group_id=created.group.id,
            group_name=created.group.name,
            is_group=True,
            reply_to_id=999,
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    bot = FakeBot()
    report = await deliver_outgoing(bot, session, reply, "WhisperBot")
    chat_ids = [chat for chat, _text in bot.sent]
    assert bob.id in chat_ids
    assert carol.id not in chat_ids
    assert report.sent == 1


# --- reply / extend / delete ------------------------------------------------


async def test_reply_flow_creates_whisper(session, cipher):
    from bot.handlers.whispers import reply_text

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
            text="вопрос",
            recipient_id=bob.id,
            recipient_username="bob",
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    state = FakeState()
    await state.update_data(token=whisper.token)
    bot = FakeBot()
    message = FakeMessage(2, text="ответный текст", username="bob")
    await reply_text(message, state, session, bob, cipher, bot, "WhisperBot")
    assert any("Ответ отправлен" in item for item in message.answers)
    replies = await repo.list_sent(session, cipher.hmac_sender(bob.id))
    assert replies and replies[0].reply_to_id == whisper.id
    assert replies[0].recipient_id == alice.id
    assert bot.sent and bot.sent[0][0] == alice.id


async def test_extend_and_delete_sender_only(session, cipher):
    from bot.handlers.whispers import cb_wact

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
            text="удали меня",
            recipient_id=bob.id,
            recipient_username="bob",
            ttl_explicit=True,
            ttl_seconds=3600,
        ),
    )
    before = whisper.expires_at
    status = await extend_ttl(session, whisper, bob.id, 3600, cipher)
    assert status == "denied"
    status = await extend_ttl(session, whisper, alice.id, 3600, cipher)
    assert status == "ok"
    assert whisper.expires_at == before + timedelta(hours=1)
    assert whisper.ttl_seconds == 7200
    forever = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="навсегда",
            recipient_id=bob.id,
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    assert await extend_ttl(session, forever, alice.id, 3600, cipher) == "forever"
    bot = FakeBot()
    cb = FakeCallback(f"wact:{whisper.token}:ext:86400", user_id=1, message=FakeMessage(1))
    await cb_wact(cb, session, alice, cipher, bot, "WhisperBot")
    assert whisper.ttl_seconds == 7200 + 86400
    denied = FakeCallback(f"wact:{whisper.token}:del", user_id=2, username="bob")
    await cb_wact(denied, session, bob, cipher, bot, "WhisperBot")
    assert whisper.status == "active"
    ok = FakeCallback(f"wact:{whisper.token}:del", user_id=1, message=FakeMessage(1))
    await cb_wact(ok, session, alice, cipher, bot, "WhisperBot")
    assert whisper.status == "destroyed"
    assert whisper.text is None
    assert await delete_by_sender(session, forever, alice.id, cipher) == "ok"


# --- pins / search ----------------------------------------------------------


async def test_pins_and_search(session, cipher):
    from bot.handlers.misc import cmd_pins, cmd_search

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
            text="пингвин ищет ключи",
            recipient_id=bob.id,
            recipient_username="bob",
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    await repo.add_pin(session, bob.id, whisper.id, utcnow())
    assert await repo.is_pinned(session, bob.id, whisper.id)
    pins = await repo.list_pins(session, bob.id)
    assert pins and pins[0][1].id == whisper.id
    message = FakeMessage(2, username="bob")
    await cmd_pins(message, session, bob, FakeState())
    assert message.answers
    empty = FakeMessage(2, username="bob")
    await cmd_pins(empty, session, alice, FakeState())
    assert any("пусто" in item for item in empty.answers)
    assert await repo.remove_pin(session, bob.id, whisper.id)
    search_msg = FakeMessage(1)
    await cmd_search(search_msg, SimpleNamespace(args="пингвин"), session, alice, cipher, FakeState())
    assert any("Результаты" in item for item in search_msg.answers)
    miss = FakeMessage(1)
    await cmd_search(miss, SimpleNamespace(args="енот"), session, alice, cipher, FakeState())
    assert any("Ничего не найдено" in item for item in miss.answers)
    usage = FakeMessage(1)
    await cmd_search(usage, SimpleNamespace(args=None), session, alice, cipher, FakeState())
    assert any("Использование" in item for item in usage.answers)


# --- web preview ------------------------------------------------------------


def test_web_link_roundtrip():
    from tests.conftest import dt

    now = dt("2026-04-01 12:00:00")
    link = make_web_link("https://preview.example/", "tok", "secret", now, ttl_sec=300)
    assert link.startswith("https://preview.example/w/tok?")
    exp = int(now.timestamp()) + 300
    sig = link.split("sig=")[1]
    assert web_link_valid("", "tok", "secret", exp, sig, now)
    assert not web_link_valid("", "tok", "secret", exp, sig, now + timedelta(seconds=301))
    assert not web_link_valid("", "tok", "other", exp, sig, now)
    assert not web_link_valid("", "tok", "secret", exp, "bad", now)


async def test_web_callback_disabled_and_enabled(session, cipher):
    from bot.handlers.whispers import cb_web

    alice = await make_user(session, 1, "alice")
    whisper = await create_whisper(
        session,
        cipher,
        NewWhisper(
            sender_id=alice.id,
            sender_username="alice",
            pseudonym=None,
            lang="ru",
            text="веб",
            recipient_id=alice.id,
            ttl_explicit=True,
            ttl_seconds=None,
        ),
    )
    cb = FakeCallback(f"web:{whisper.token}", user_id=1, message=FakeMessage(1))
    await cb_web(cb, session, alice, cipher)
    assert any("Веб-превью выключено" in (text or "") for text, _ in cb.alerts)
    cb2 = FakeCallback(f"web:{whisper.token}", user_id=1, message=FakeMessage(1))
    await cb_web(cb2, session, alice, cipher, web_base_url="https://preview.example")
    assert any("5 минут" in (text or "") for text, _ in cb2.alerts)


# --- contacts display / favourites ------------------------------------------


async def test_contacts_favorites_and_names(session):
    alice = await make_user(session, 1, "alice")
    await make_user(session, 7, "carol", first_name="Кэрол", last_name="Смит")
    await repo.upsert_contact(session, alice.id, "bobby", 2, utcnow())
    contact = (await repo.list_contacts(session, alice.id))[0]
    await repo.set_contact_favorite(session, alice.id, contact.id, True)
    await repo.upsert_contact(session, alice.id, "carol", 7, utcnow())
    listed = await repo.list_contacts(session, alice.id)
    assert listed[0].favorite is True
    assert listed[0].username == "bobby"
    assert listed[1].first_name == "Кэрол"
    from bot.services.views import display_user

    assert display_user("bobby", 2, "ru") == "@bobby"
    assert display_user(None, 2, "ru", "Кэрол", "Смит") == "Кэрол Смит"
    assert display_user(None, 2, "ru") == "tg://user?id=2"


# --- groups: auto-join + export ---------------------------------------------


def test_group_invite_payload_order():
    payload = parse_start_payload("grp_invite_abcdefghijkl")
    assert payload.kind == "group_invite"
    assert payload.token == "abcdefghijkl"
    assert parse_start_payload("grp_15").kind == "group"
    assert group_invite_link("Bot", "tok123456").endswith("start=grp_invite_tok123456")


async def test_group_autojoin_and_export(session, cipher):
    from bot.handlers.groups import on_group
    from bot.handlers.start import show_group_invite

    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    from bot.services.group_service import create_group

    created = await create_group(session, name="Автогруппа", creator_id=alice.id, creator_username="alice")
    group = created.group
    bot = FakeBot()
    await on_group(
        FakeCallback(f"grp:autojoin:{group.id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState()
    )
    await session.flush()
    assert group.auto_join and group.invite_token
    message = FakeMessage(2, username="bob")
    await show_group_invite(message, session, bob, "WhisperBot", group.invite_token)
    assert await repo.is_member(session, group.id, bob.id)
    assert any("вступили" in item.lower() for item in message.answers)
    again = FakeMessage(2, username="bob")
    await show_group_invite(again, session, bob, "WhisperBot", group.invite_token)
    assert again.answers
    missing = FakeMessage(2, username="bob")
    await show_group_invite(missing, session, bob, "WhisperBot", "nope-token-value")
    assert any("не найдена" in item for item in missing.answers)
    export_cb = FakeCallback(f"grp:export:{group.id}", message=FakeMessage(1))
    await on_group(export_cb, session, alice, bot, "WhisperBot", FakeState())
    assert any(getattr(item, "answers", None) for item in [export_cb.message])
    await on_group(
        FakeCallback(f"grp:autojoin:{group.id}", message=FakeMessage(1)), session, alice, bot, "WhisperBot", FakeState()
    )
    assert group.auto_join is False


# --- envelope labels + send integration -------------------------------------


def test_envelope_label_replaces_lock():
    from bot.services.views import envelope_text

    plain = envelope_text(lang="ru", recipient_username="bob", ttl_seconds=None)
    assert plain.startswith("🔒")
    labelled = envelope_text(lang="ru", recipient_username="bob", ttl_seconds=None, label="🕵️")
    assert labelled.startswith("🕵️")
    assert "Секретное сообщение только для @bob" in labelled
    assert "🕵️" in ENVELOPE_LABELS


async def test_send_with_media_key_and_label(session, cipher):
    from bot.handlers.send import content_text, pick_label, send_ok

    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    key_result = await convert.create_key(
        session, owner_id=alice.id, files=[{"type": "document", "file_id": "DOC1"}], now=utcnow()
    )
    state = FakeState()
    bot = FakeBot()
    message = FakeMessage(1, text=f'вот файл MEDIA_ID="{key_result.key}"')
    await content_text(message, state, alice, session, cipher)
    assert state.data.get("media_key") == key_result.key
    assert state.data.get("text") == "вот файл"
    await pick_label(FakeCallback("send:label:💌", message=FakeMessage(1)), state, alice)
    assert state.data.get("label") == "💌"
    await state.update_data(
        recipient_type="user", recipient_id=bob.id, recipient_username="bob", mode="open", ttl_set=True, ttl_code="inf"
    )
    done = FakeCallback("send:ok", message=FakeMessage(1))
    await send_ok(done, state, session, alice, cipher, bot, "WhisperBot")
    sent = await repo.list_sent(session, cipher.hmac_sender(alice.id))
    assert sent[0].media_key == key_result.key
    assert sent[0].label == "💌"

    wrong = FakeMessage(1, text='MEDIA_ID="ABC234"')
    state2 = FakeState()
    await content_text(wrong, state2, alice, session, cipher)
    assert any("не найдено" in item for item in wrong.answers)
    other = await make_user(session, 5, "mallory")
    foreign = await convert.create_key(session, owner_id=other.id, files=[{"type": "photo", "file_id": "X"}], now=utcnow())
    state3 = FakeState()
    bad = FakeMessage(1, text=f'MEDIA_ID="{foreign.key}"')
    await content_text(bad, state3, alice, session, cipher)
    assert any("не ваш" in item for item in bad.answers)


async def test_send_sealed_via_e2e_word(session, cipher):
    from bot.handlers.send import content_text, send_ok

    alice = await make_user(session, 1, "alice")
    bob = await make_user(session, 2, "bob")
    state = FakeState()
    bot = FakeBot()
    message = FakeMessage(1, text="e2e очень секретно")
    await content_text(message, state, alice, session, cipher)
    assert state.data.get("sealed") is True
    assert state.data.get("text") == "очень секретно"
    await state.update_data(
        recipient_type="user", recipient_id=bob.id, recipient_username="bob", mode="open", ttl_set=True, ttl_code="inf"
    )
    done = FakeCallback("send:ok", message=FakeMessage(1))
    await send_ok(done, state, session, alice, cipher, bot, "WhisperBot")
    sent = await repo.list_sent(session, cipher.hmac_sender(alice.id))
    assert sent[0].is_sealed
    assert "секретно" not in (sent[0].text or "")
    assert sent[0].seal_key_enc is None  # shown once then erased
    assert any("Ключ запечатывания" in text for _chat, text in bot.sent)


# --- convert handlers -------------------------------------------------------


async def test_convert_handlers_single_and_album(session):
    from bot.handlers.convert import cmd_convert, cmd_myconvert, cb_convert_del, on_album_item, on_convert_media

    alice = await make_user(session, 1, "alice")
    state = FakeState()
    message = FakeMessage(1)
    await cmd_convert(message, state, alice)
    assert state.state is not None
    photo = FakeMessage(1)
    photo.photo = [SimpleNamespace(file_id="P1", file_size=100)]
    await on_convert_media(photo, session, alice, state)
    assert any("<code>" in item for item in photo.answers)
    assert any('MEDIA_ID="' in item for item in photo.answers)

    album_state = FakeState()
    await cmd_convert(FakeMessage(1), album_state, alice)
    first = FakeMessage(1)
    first.photo = [SimpleNamespace(file_id="A1", file_size=10)]
    first.media_group_id = "g42"
    second = FakeMessage(1)
    second.photo = [SimpleNamespace(file_id="A2", file_size=10)]
    second.media_group_id = "g42"
    await on_album_item(first, session, alice, album_state)
    await on_album_item(second, session, alice, album_state)
    assert sum(1 for item in first.answers + second.answers if "Ключ медиа" in item) == 1
    album_row = await repo.get_media_key_by_group(session, "1:g42")
    assert album_row is not None
    assert len(convert.loads_files(album_row.files)) == 2

    listing = FakeMessage(1)
    await cmd_myconvert(listing, session, alice, FakeState())
    assert any("Мои медиа-ключи" in item for item in listing.answers)
    keys = await repo.list_media_keys(session, alice.id)
    for row in keys:
        await cb_convert_del(FakeCallback(f"conv:del:{row.key}", message=FakeMessage(1)), session, alice)
    assert (await repo.list_media_keys(session, alice.id)) == []
    empty = FakeMessage(1)
    await cmd_myconvert(empty, session, alice, FakeState())
    assert any("нет" in item.lower() for item in empty.answers)


# --- settings: seal toggle + quiet hours ------------------------------------


async def test_settings_seal_and_quiet(session):
    from bot.handlers.settings import ask_quiet, save_quiet, set_seal

    alice = await make_user(session, 1, "alice")
    await set_seal(FakeCallback("set:seal", message=FakeMessage(1)), alice)
    assert alice.seal_default is True
    state = FakeState()
    await ask_quiet(FakeCallback("set:quiet", message=FakeMessage(1)), alice, state)
    await save_quiet(FakeMessage(1, text="23:00 07:00"), state, alice)
    assert alice.quiet_from == "23:00" and alice.quiet_to == "07:00"
    state2 = FakeState()
    await ask_quiet(FakeCallback("set:quiet", message=FakeMessage(1)), alice, state2)
    await save_quiet(FakeMessage(1, text="криво"), state2, alice)
    assert alice.quiet_from == "23:00"
    state3 = FakeState()
    await ask_quiet(FakeCallback("set:quiet", message=FakeMessage(1)), alice, state3)
    await save_quiet(FakeMessage(1, text="выкл"), state3, alice)
    assert alice.quiet_from is None and alice.quiet_to is None
