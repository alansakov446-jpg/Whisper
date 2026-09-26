from __future__ import annotations

from bot.services.inline_plan import build_inline_plan
from bot.services.parser import parse_inline_query
from bot.services.views import envelope_buttons, envelope_text, group_buttons, main_menu, settings_keyboard
from bot.services.i18n import t


class DummyUser:
    def __init__(self):
        self.default_ttl = None
        self.default_mode = "open"
        self.notifications = True
        self.pseudonym = None
        self.lang = "ru"


def test_inline_requires_activation():
    parsed = parse_inline_query("hi @bobuser")
    plan = build_inline_plan(
        parsed,
        lang="ru",
        activated=False,
        rate_limited=False,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym=None,
        default_ttl=None,
        saved_usernames=set(),
        saved_ids=set(),
        contacts=[],
        groups=[],
    )
    assert plan.needs_activation
    assert plan.articles[0].title == "⚠️ Сначала активируйте бота"
    assert plan.button_text == "🤖 Перейти к боту"
    assert plan.articles[0].buttons[0][0].text == "🤖 Перейти к боту"


def test_inline_single_and_saved_contact():
    parsed = parse_inline_query("привет друг, это секрет! @bobuser")
    plan = build_inline_plan(
        parsed,
        lang="ru",
        activated=True,
        rate_limited=False,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym="Тень",
        default_ttl=None,
        saved_usernames={"bobuser"},
        saved_ids=set(),
        contacts=[],
        groups=[],
    )
    card = plan.articles[0]
    assert card.title == "🔒 Отправить секретное сообщение для @bobuser"
    assert card.description == "📇 Из сохранённых"
    assert "🔒 Секретное сообщение от Тень" in card.message_text
    assert "🔒 Секретное сообщение только для @bobuser" in card.message_text
    assert card.buttons[0][0].text == "🔓 Открыть сообщение"
    assert card.draft is not None
    fresh = build_inline_plan(
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
    )
    assert fresh.articles[0].description == "➕ Новый контакт"


def test_inline_group_and_media_and_limit():
    parsed = parse_inline_query("текст @userone @usertwo @userthree")
    plan = build_inline_plan(
        parsed,
        lang="ru",
        activated=True,
        rate_limited=False,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym=None,
        default_ttl=3600,
        saved_usernames=set(),
        saved_ids=set(),
        contacts=[],
        groups=[],
        media_type="photo",
        media_file_id="abc",
    )
    card = plan.articles[0]
    assert card.title == "🔒 Отправить шёпот группе (4 участников)"
    assert "➕ Создать новую группу" in card.description
    assert "📩 Открыть в боте" in card.description
    assert card.buttons[0][0].url.startswith("https://t.me/WhisperBot?start=w_")
    assert card.draft.media_file_id == "abc"
    limited = build_inline_plan(
        parsed,
        lang="ru",
        activated=True,
        rate_limited=True,
        bot_username="WhisperBot",
        sender_username="alice",
        pseudonym=None,
        default_ttl=None,
        saved_usernames=set(),
        saved_ids=set(),
        contacts=[],
        groups=[],
    )
    assert limited.articles[0].draft is None
    assert "30" in limited.articles[0].message_text


def test_envelope_modes_and_menus():
    anon = envelope_text(lang="ru", is_anonymous=True, delivery_mode="anonymous", ttl_seconds=None)
    assert anon.startswith("🔒 Анонимный шёпот. Кто-то отправил вам сообщение.")
    opened = envelope_text(
        lang="ru",
        delivery_mode="open",
        sender_username="alice",
        ttl_seconds=60,
    )
    assert "🔒 Секретное сообщение от @alice" in opened
    buttons = envelope_buttons(lang="ru", token="tok", bot_username="Bot", has_media=False)
    assert buttons[0][0].callback_data == "open:tok"
    media = envelope_buttons(lang="ru", token="tok", bot_username="Bot", has_media=True)
    assert media[0][0].text == "📩 Открыть в боте"
    menu = main_menu("ru")
    labels = [button.text for row in menu for button in row]
    assert labels == [
        "🔒 Отправить шёпот",
        "⚙️ Настройки",
        "📇 Мои контакты",
        "👥 Мои группы",
        "ℹ️ Инструкция",
    ]
    user = DummyUser()
    settings = settings_keyboard("ru", user)
    flat = [button.text for row in settings for button in row]
    assert any("Навсегда" in item for item in flat)
    assert any("Анонимно" in item for item in flat)
    assert any("Открыто" in item for item in flat)
    assert any("Уведомления" in item for item in flat)

    class G:
        id = 7
        creator_id = 1
        name = "A"

    creator_buttons = [button.text for row in group_buttons("ru", G(), 1) for button in row]
    assert "✏️ Изменить название" in creator_buttons
    assert "➖ Удалить пользователя" in creator_buttons
    assert "➕ Добавить пользователя" in creator_buttons
    assert "👑 Передать наследство" in creator_buttons
    assert "🚪 Покинуть группу" in creator_buttons
    guest = [button.text for row in group_buttons("ru", G(), 2) for button in row]
    assert "✏️ Изменить название" not in guest
    assert "🚪 Покинуть группу" in guest
    assert t("ru", "inline_title_group", n=3) == "🔒 Отправить шёпот группе (3 участников)"
