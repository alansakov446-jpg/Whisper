from __future__ import annotations

from datetime import timedelta

import pytest

from bot.constants import RATE_LIMIT_COUNT
from bot.services.access import AccessContext, check_access, is_sender
from bot.services.crypto import Cipher
from bot.services.deeplink import parse_start_payload, whisper_link
from bot.services.i18n import STRINGS, format_ttl, instruction, t
from bot.services.parser import parse_identity, parse_inline_query
from bot.services.ttl import DurationError, compute_expires, parse_duration, parse_schedule, resolve_ttl, validate_ttl
from bot.utils.text import norm_username, parse_cgn_args, parse_pseudonym
from bot.utils.time import utcnow
from tests.conftest import dt


def test_i18n_parity_and_spec_strings():
    assert set(STRINGS["ru"]) == set(STRINGS["en"])
    assert t("ru", "not_yours") == "❌ Это не ваше сообщение"
    assert t("ru", "destroyed") == "💥 Шёпот самоуничтожен"
    assert t("ru", "activate_title") == "⚠️ Сначала активируйте бота"
    assert t("ru", "activate_button") == "🤖 Перейти к боту"
    assert t("ru", "open_button") == "🔓 Открыть сообщение"
    assert t("ru", "open_in_bot") == "📩 Открыть в боте"
    assert t("ru", "inline_new_contact") == "➕ Новый контакт"
    assert t("ru", "inline_saved_contact") == "📇 Из сохранённых"
    assert t("ru", "inline_create_group") == "➕ Создать новую группу"
    assert t("ru", "envelope_anonymous") == "🔒 Анонимный шёпот. Кто-то отправил вам сообщение."
    assert t("ru", "anon_note") == "Inline не скрывает отправителя — для полной анонимности используйте /send"
    text = instruction("ru", "WhisperBot")
    assert "Inline не скрывает отправителя — для полной анонимности используйте /send" in text
    assert "@WhisperBot" in text
    assert t("de", "not_yours") == t("ru", "not_yours")
    assert format_ttl(None, "ru") == "♾ Навсегда"
    assert format_ttl(60, "ru") == "1 мин"
    assert format_ttl(90, "ru") == "90 с"


def test_duration_and_ttl_priority():
    assert parse_duration("навсегда") is None
    assert parse_duration("1h") == 3600
    assert parse_duration("2h30m") == 9000
    assert parse_duration("1д") == 86400
    assert parse_duration("90") == 90
    with pytest.raises(DurationError):
        parse_duration("soon")
    with pytest.raises(DurationError):
        validate_ttl(1)
    assert resolve_ttl(explicit=True, chosen=60, default=3600) == 60
    assert resolve_ttl(explicit=True, chosen=None, default=3600) is None
    assert resolve_ttl(explicit=False, chosen=60, default=3600) == 3600
    assert resolve_ttl(explicit=False, chosen=None, default=None) is None
    base = dt("2026-04-01 00:00:00")
    assert compute_expires(base, None) is None
    assert compute_expires(base, 60) == base + timedelta(seconds=60)
    now = dt("2026-04-01 12:00:00")
    assert parse_schedule("сейчас", now) == now
    assert parse_schedule("через 10m", now) == now + timedelta(minutes=10)
    assert parse_schedule("2026-04-02 15:00", now) == dt("2026-04-02 15:00:00")


def test_parser_and_helpers():
    parsed = parse_inline_query("привет друг, это секрет! @username_friend ttl:1h", bot_username="WhisperBot")
    assert parsed.text == "привет друг, это секрет!"
    assert parsed.usernames == ["username_friend"]
    assert parsed.ttl_explicit is True
    assert parsed.ttl_seconds == 3600
    group = parse_inline_query("текст @userone @usertwo @userthree")
    assert group.usernames == ["userone", "usertwo", "userthree"]
    assert group.text == "текст"
    named = parse_inline_query("hello #Family")
    assert named.group_ref == "Family"
    assert named.text == "hello"
    ident = parse_identity("@SomeUser")
    assert ident.username == "someuser"
    assert parse_identity("id:42").user_id == 42
    assert parse_identity("12345").user_id == 12345
    assert norm_username("@Ab") is None
    assert parse_cgn_args('  "Моя группа" ') == "Моя группа"
    assert parse_cgn_args(None) is None
    ok, value = parse_pseudonym("-")
    assert ok and value is None
    ok, value = parse_pseudonym("Тень")
    assert ok and value == "Тень"


def test_crypto_crypto_payloads():
    assert parse_start_payload(None).kind == "none"
    assert parse_start_payload("help").kind == "help"
    payload = parse_start_payload("w_abcdefghijklmnop")
    assert payload.kind == "whisper" and payload.token == "abcdefghijklmnop"
    assert parse_start_payload("ttl_abcdefghijklmnop").kind == "ttl"
    assert parse_start_payload("grp_15").group_id == 15
    assert parse_start_payload("nope").kind == "unknown"
    assert whisper_link("Bot", "abc").endswith("start=w_abc")


def test_crypto_crypto_cipher_roundtrip():
    cipher = Cipher.__new__(Cipher)
    from cryptography.fernet import Fernet

    cipher = Cipher(Fernet.generate_key().decode())
    encrypted = cipher.encrypt_sender(4242)
    assert cipher.decrypt_sender(encrypted) == 4242
    assert cipher.hmac_matches(4242, cipher.hmac_sender(4242))
    assert not cipher.hmac_matches(1, cipher.hmac_sender(4242))
    assert "4242" not in encrypted
    with pytest.raises(ValueError):
        cipher.decrypt_sender("not-a-token")


def test_access_rules(cipher):
    class W:
        sender_id = 1
        sender_id_enc = None
        sender_hmac = cipher.hmac_sender(1)
        recipient_id = 2
        recipient_username = "bob"
        is_group = False
        status = "active"
        expires_at = None
        send_at = utcnow()

    now = utcnow()
    assert check_access(W(), AccessContext(2, "bob"), cipher, now) == "allow"
    assert check_access(W(), AccessContext(1, "alice"), cipher, now) == "allow"
    assert check_access(W(), AccessContext(3, "eve"), cipher, now) == "deny"
    stranger = W()
    stranger.recipient_id = None
    assert check_access(stranger, AccessContext(2, "Bob"), cipher, now) == "allow"
    expired = W()
    expired.expires_at = now - timedelta(seconds=1)
    assert check_access(expired, AccessContext(2, "bob"), cipher, now) == "destroyed"
    forever = W()
    forever.expires_at = None
    forever.ttl_seconds = None
    assert check_access(forever, AccessContext(2, "bob"), cipher, now) == "allow"
    group = W()
    group.is_group = True
    group.recipient_id = None
    assert check_access(group, AccessContext(9, is_group_member=True), cipher, now) == "allow"
    assert check_access(group, AccessContext(1, is_group_member=False), cipher, now) == "deny"
    assert is_sender(group, 1, cipher)


def test_constants_rate_window():
    assert RATE_LIMIT_COUNT == 30
