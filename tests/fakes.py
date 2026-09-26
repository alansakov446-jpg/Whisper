from __future__ import annotations

from types import SimpleNamespace


class FakeMessage:
    def __init__(self, user_id: int = 1, text: str = "", chat_type: str = "private", username: str = "alice"):
        self.from_user = SimpleNamespace(id=user_id, username=username, is_bot=False, language_code="ru")
        self.chat = SimpleNamespace(id=user_id, type=chat_type)
        self.text = text
        self.caption = None
        self.photo = None
        self.video = None
        self.document = None
        self.animation = None
        self.voice = None
        self.audio = None
        self.video_note = None
        self.answers: list[str] = []

    async def answer(self, text, **kwargs):
        self.answers.append(text)
        return SimpleNamespace(message_id=len(self.answers), chat=self.chat)

    async def answer_document(self, document, **kwargs):
        self.answers.append(kwargs.get("caption") or "doc")
        return SimpleNamespace(message_id=100)

    async def edit_text(self, text, **kwargs):
        self.answers.append(text)
        return SimpleNamespace(message_id=1)


class FakeCallback:
    def __init__(self, data: str, user_id: int = 1, message: FakeMessage | None = None, username: str = "alice"):
        self.data = data
        self.from_user = SimpleNamespace(id=user_id, username=username, is_bot=False)
        self.message = message or FakeMessage(user_id=user_id, username=username)
        self.inline_message_id = None
        self.alerts: list[tuple] = []

    async def answer(self, text=None, show_alert=False, **kwargs):
        self.alerts.append((text, show_alert))


class FakeState:
    def __init__(self):
        self.data: dict = {}
        self.state = None

    async def clear(self):
        self.data = {}
        self.state = None

    async def set_state(self, state):
        self.state = state

    async def get_state(self):
        return self.state.state if self.state is not None and hasattr(self.state, "state") else self.state

    async def update_data(self, **kwargs):
        self.data.update(kwargs)

    async def set_data(self, data):
        self.data = dict(data)

    async def get_data(self):
        return dict(self.data)


class FakeBot:
    def __init__(self, fail_ids: set[int] | None = None):
        self.fail_ids = fail_ids or set()
        self.sent: list[tuple] = []
        self.edits: list[tuple] = []
        self.media: list[tuple] = []

    async def send_message(self, chat_id, text, **kwargs):
        if chat_id in self.fail_ids:
            raise RuntimeError("forbidden")
        self.sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(self.sent) + 1, chat=SimpleNamespace(id=chat_id))

    async def edit_message_text(self, text, **kwargs):
        self.edits.append((text, kwargs))
        return True

    async def _media(self, kind, chat_id, file_id, **kwargs):
        self.media.append((kind, chat_id, file_id))
        return SimpleNamespace(message_id=80)

    async def send_photo(self, chat_id, photo=None, **kwargs):
        return await self._media("photo", chat_id, photo, **kwargs)

    async def send_video(self, chat_id, video=None, **kwargs):
        return await self._media("video", chat_id, video, **kwargs)

    async def send_document(self, chat_id, document=None, **kwargs):
        return await self._media("document", chat_id, document, **kwargs)

    async def send_animation(self, chat_id, animation=None, **kwargs):
        return await self._media("animation", chat_id, animation, **kwargs)

    async def send_voice(self, chat_id, voice=None, **kwargs):
        return await self._media("voice", chat_id, voice, **kwargs)

    async def send_audio(self, chat_id, audio=None, **kwargs):
        return await self._media("audio", chat_id, audio, **kwargs)

    async def send_video_note(self, chat_id, video_note=None, **kwargs):
        return await self._media("video_note", chat_id, video_note, **kwargs)

    async def set_my_commands(self, *args, **kwargs):
        return True

    async def set_my_description(self, *args, **kwargs):
        return True

    async def set_my_short_description(self, *args, **kwargs):
        return True

    async def get_me(self):
        return SimpleNamespace(username="WhisperBot", id=1)


class FakeQuery:
    def __init__(self, text: str, user_id: int = 1):
        self.query = text
        self.from_user = SimpleNamespace(id=user_id, username="alice", is_bot=False)
        self.results = None
        self.kwargs = None

    async def answer(self, results, **kwargs):
        self.results = results
        self.kwargs = kwargs


class FakeChosen:
    def __init__(self, result_id: str, inline_message_id: str | None = "inline-1"):
        self.result_id = result_id
        self.inline_message_id = inline_message_id
        self.from_user = SimpleNamespace(id=1, username="alice", is_bot=False)
