from __future__ import annotations

import sys
from pathlib import Path

from loguru import logger

from bot.config import Settings


def setup_logging(settings: Settings) -> None:
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level.upper(), enqueue=False)
    log_dir = Path("logs")
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        log_dir / "bot_{time:YYYY-MM-DD}.log",
        level="DEBUG",
        rotation="00:00",
        retention="7 days",
        enqueue=True,
        encoding="utf-8",
    )
