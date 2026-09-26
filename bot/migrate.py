from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config

ROOT = Path(__file__).resolve().parents[1]


def run_migrations(database_url: str) -> None:
    os.environ["WHISPER_MIGRATE_URL"] = database_url
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head")
