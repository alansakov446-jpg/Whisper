from __future__ import annotations

import re

RATE_LIMIT_COUNT = 30
RATE_LIMIT_WINDOW_SEC = 60
ALERT_MAX = 180
PAGE_SIZE = 3500
CAPTION_LIMIT = 1024
MAX_TEXT_LEN = 100_000
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MIN_TTL_SECONDS = 10
MAX_TTL_SECONDS = 10 * 365 * 24 * 3600
PSEUDONYM_MAX = 32
GROUP_NAME_MAX = 64
DRAFT_TTL_HOURS = 24
TOKEN_BYTES = 16

TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
USERNAME_RE = re.compile(r"^[a-z][a-z0-9_]{4,31}$")

TTL_PRESETS: dict[str, int | None] = {
    "inf": None,
    "60": 60,
    "3600": 3600,
    "86400": 86400,
    "604800": 604800,
}

ACTIVE_STATUSES = ("active", "scheduled", "destroyed", "delivering", "failed")
SEND_STATUSES = ("active", "scheduled", "destroyed", "delivering", "failed")
