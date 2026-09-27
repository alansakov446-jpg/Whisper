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

# --- v2: media keys (/convert) ---------------------------------------------
MEDIA_KEY_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
MEDIA_KEY_LEN = 6
MEDIA_KEY_RE = re.compile(r"^[A-HJ-NP-Z2-9]{6}$")
MEDIA_ID_RE = re.compile(r'\bMEDIA_ID\s*=\s*"?([A-HJ-NP-Z2-9]{6})"?', re.IGNORECASE)
MAX_INLINE_IDS = 50
MAX_ALBUM_FILES = 10
CONVERT_RATE_COUNT = 20
CONVERT_RATE_WINDOW_SEC = 3600
MEDIA_KEY_TTL_DAYS = 30

# --- v2: inline id= recipients ---------------------------------------------
INLINE_ID_RE = re.compile(r"\bid=(\d{5,15})\b")

# --- v2: envelope labels ---------------------------------------------------
ENVELOPE_LABELS = ("🔒", "🕵️", "💌", "🎁", "🤫", "💀")
LABEL_RE = re.compile(r"(?:^|\s)(?:label:|метка:)([🔒🕵💌🎁🤫💀](?:\ufe0f)?)\s*", re.IGNORECASE)
E2E_RE = re.compile(r"(?:^|\s)e2e(?=\s|$)", re.IGNORECASE)

# --- v2: sealed whispers (e2e) ---------------------------------------------
SEAL_KEY_BYTES = 12

# --- v2: web preview -------------------------------------------------------
WEB_LINK_TTL_SEC = 300

# --- v2: TTL extend --------------------------------------------------------
EXTEND_PRESETS = (3600, 86400)
