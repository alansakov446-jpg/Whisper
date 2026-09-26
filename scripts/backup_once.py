"""One-shot pg_dump → private git repo.

No-op (exit 0) when the database is not Postgres, BACKUP_REPO_URL is empty,
or pg_dump is not installed. Used by the hourly GitHub Actions workflow.
"""

from __future__ import annotations

import sys

from bot.config import get_settings
from bot.services.backup import run_backup


def main() -> int:
    result = run_backup(get_settings())
    print(result)
    return 0 if result == "ok" or str(result).startswith("skip-") else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"backup error: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
