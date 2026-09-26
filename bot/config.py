from __future__ import annotations

import os
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    bot_token: str = ""
    database_url: str = "sqlite+aiosqlite:///./data/whisper.db"
    fernet_key: str = ""
    health_host: str = "0.0.0.0"
    health_port: int = 8080
    heartbeat_url: str = ""
    backup_repo_url: str = ""
    backup_interval_minutes: int = 30
    log_level: str = "INFO"
    fernet_key_path: str = Field(default="data/fernet.key")


def get_settings() -> Settings:
    return Settings()


def validate_settings(settings: Settings, *, github_actions: bool | None = None) -> list[str]:
    errors: list[str] = []
    token = (settings.bot_token or "").strip()
    if not token:
        errors.append("BOT_TOKEN is empty. Create a bot in @BotFather and put the token in the environment.")
    elif ":" not in token:
        errors.append("BOT_TOKEN does not look like a Telegram bot token.")
    on_actions = os.getenv("GITHUB_ACTIONS") == "true" if github_actions is None else github_actions
    url = (settings.database_url or "").strip()
    if on_actions and not url.startswith("postgres"):
        errors.append("DATABASE_URL must be a Neon/Postgres URL when running in GitHub Actions.")
    if settings.backup_interval_minutes < 0:
        errors.append("BACKUP_INTERVAL_MINUTES must be >= 0.")
    if not (1 <= settings.health_port <= 65535):
        errors.append("HEALTH_PORT is out of range.")
    return errors


def resolve_fernet_key(settings: Settings, *, allow_generate: bool = True) -> str:
    raw = (settings.fernet_key or "").strip()
    if raw:
        Fernet(raw.encode())
        return raw
    path = Path(settings.fernet_key_path)
    if path.exists():
        saved = path.read_text(encoding="utf-8").strip()
        Fernet(saved.encode())
        return saved
    if not allow_generate or os.getenv("GITHUB_ACTIONS") == "true":
        raise RuntimeError("FERNET_KEY is required. Generate one with: python scripts/generate_fernet_key.py")
    path.parent.mkdir(parents=True, exist_ok=True)
    key = Fernet.generate_key().decode()
    path.write_text(key, encoding="utf-8")
    return key
