from __future__ import annotations

import os
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

from loguru import logger

from bot.config import Settings
from bot.utils.time import utcnow


def postgres_dump_command(database_url: str, output_path: str) -> list[str]:
    return [
        "pg_dump",
        "--no-owner",
        "--no-acl",
        "--format=plain",
        "--file",
        output_path,
        database_url,
    ]


def backup_skip_reason(settings: Settings) -> str | None:
    url = settings.database_url or ""
    if not url.startswith("postgres"):
        return "skip-not-postgres"
    if not settings.backup_repo_url:
        return "skip-no-repo"
    if shutil.which("pg_dump") is None:
        return "skip-no-pgdump"
    return None


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env.setdefault("GIT_AUTHOR_NAME", "Whisper Backup")
    env.setdefault("GIT_AUTHOR_EMAIL", "backup@whisper.local")
    env.setdefault("GIT_COMMITTER_NAME", "Whisper Backup")
    env.setdefault("GIT_COMMITTER_EMAIL", "backup@whisper.local")
    return subprocess.run(
        ["git", "-C", str(repo_dir), *args],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


def push_dump_to_repo(repo_url: str, dump_path: Path, work_dir: Path) -> None:
    work_dir.mkdir(parents=True, exist_ok=True)
    repo_dir = work_dir / "repo"
    if repo_dir.exists():
        shutil.rmtree(repo_dir)
    clone = subprocess.run(
        ["git", "clone", repo_url, str(repo_dir)],
        check=False,
        capture_output=True,
        text=True,
    )
    if clone.returncode != 0:
        repo_dir.mkdir(parents=True, exist_ok=True)
        init = _git(repo_dir, "init")
        init.check_returncode()
        _git(repo_dir, "remote", "add", "origin", repo_url)
    _git(repo_dir, "config", "user.email", "backup@whisper.local")
    _git(repo_dir, "config", "user.name", "Whisper Backup")
    target = repo_dir / dump_path.name
    shutil.copy2(dump_path, target)
    added = _git(repo_dir, "add", dump_path.name)
    added.check_returncode()
    commit = _git(repo_dir, "commit", "-m", f"backup {dump_path.name}")
    if commit.returncode != 0 and "nothing to commit" not in (commit.stdout + commit.stderr):
        raise subprocess.CalledProcessError(commit.returncode, commit.args, commit.stdout, commit.stderr)
    push = _git(repo_dir, "push", "origin", "HEAD")
    if push.returncode != 0:
        push = _git(repo_dir, "push", "--set-upstream", "origin", "HEAD")
    push.check_returncode()


def run_backup(settings: Settings, *, now: datetime | None = None, work_dir: Path | None = None) -> str:
    reason = backup_skip_reason(settings)
    if reason:
        logger.info("backup skipped: {}", reason)
        return reason
    now = now or utcnow()
    stamp = now.strftime("%Y%m%d_%H%M%S")
    root = work_dir or Path("backups")
    root.mkdir(parents=True, exist_ok=True)
    dump_path = root / f"whisper_{stamp}.sql"
    command = postgres_dump_command(settings.database_url, str(dump_path))
    subprocess.run(command, check=True, capture_output=True)
    push_dump_to_repo(settings.backup_repo_url, dump_path, root / "git")
    logger.info("backup stored {}", dump_path.name)
    return "ok"
