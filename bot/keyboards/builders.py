from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from bot.services.views import ButtonSpec


def markup(rows: list[list[ButtonSpec]] | None) -> InlineKeyboardMarkup | None:
    if not rows:
        return None
    keyboard = []
    for row in rows:
        buttons = []
        for spec in row:
            if spec.url:
                buttons.append(InlineKeyboardButton(text=spec.text, url=spec.url))
            elif spec.callback_data:
                buttons.append(InlineKeyboardButton(text=spec.text, callback_data=spec.callback_data))
        if buttons:
            keyboard.append(buttons)
    if not keyboard:
        return None
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
