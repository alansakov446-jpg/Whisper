from __future__ import annotations

from bot.constants import MAX_MEDIA_BYTES


def extract_media(message) -> tuple[str, str, int | None] | None:
    photo = getattr(message, "photo", None)
    if photo:
        best = photo[-1]
        return "photo", best.file_id, getattr(best, "file_size", None)
    for attr, kind in (
        ("video", "video"),
        ("document", "document"),
        ("animation", "animation"),
        ("voice", "voice"),
        ("audio", "audio"),
        ("video_note", "video_note"),
    ):
        obj = getattr(message, attr, None)
        if obj:
            return kind, obj.file_id, getattr(obj, "file_size", None)
    return None


def media_too_big(size: int | None) -> bool:
    return size is not None and size > MAX_MEDIA_BYTES
