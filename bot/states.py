from aiogram.fsm.state import State, StatesGroup


class SendFlow(StatesGroup):
    recipient = State()
    content = State()
    mode = State()
    ttl = State()
    schedule = State()
    confirm = State()


class SettingsFlow(StatesGroup):
    custom_ttl = State()
    pseudonym = State()
    quiet = State()


class GroupFlow(StatesGroup):
    rename = State()
    add_member = State()


class TtlFlow(StatesGroup):
    custom = State()


class SealFlow(StatesGroup):
    key = State()


class ReplyFlow(StatesGroup):
    text = State()


class ConvertFlow(StatesGroup):
    waiting = State()
