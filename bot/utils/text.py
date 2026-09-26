from __future__ import annotations

from bot.constants import GROUP_NAME_MAX, PSEUDONYM_MAX, USERNAME_RE


def clip(text: str | None, limit: int) -> str:
    raw = " ".join((text or "").split())
    if len(raw) <= limit:
        return raw
    if limit <= 1:
        return raw[:limit]
    return raw[: limit - 1] + "…"


def preview(text: str | None, limit: int = 200) -> str:
    return clip(text, limit)


def norm_username(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    if raw.startswith("@"):
        raw = raw[1:]
    raw = raw.strip().lower()
    if not USERNAME_RE.match(raw):
        return None
    return raw


def parse_cgn_args(args: str | None) -> str | None:
    if not args:
        return None
    text = args.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        text = text[1:-1].strip()
    if text.startswith("«") and text.endswith("»") and len(text) >= 2:
        text = text[1:-1].strip()
    text = " ".join(text.split())
    if not text:
        return None
    return text[:GROUP_NAME_MAX]


def parse_pseudonym(value: str) -> tuple[bool, str | None]:
    """Return (ok, pseudonym). None pseudonym means clear. ok False is invalid."""
    text = " ".join((value or "").split())
    if text.lower() in {"-", "—", "нет", "none", "clear", "убрать"}:
        return True, None
    if not text or len(text) > PSEUDONYM_MAX:
        return False, None
    if any(ord(ch) < 32 for ch in text):
        return False, None
    return True, text


def clean_secret(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = text.replace("\x00", "")
    if not cleaned.strip():
        return None
    return cleaned
