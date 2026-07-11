"""Verify repository ignore rules for generated and sensitive files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_GIT_TIMEOUT_SECONDS = 5.0

_IGNORED_PATHS = (
    ".env",
    ".env.local",
    ".venv/pyvenv.cfg",
    ".pytest_cache/CACHEDIR.TAG",
    ".mypy_cache/3.12/cache.json",
    ".ruff_cache/cache.db",
    ".coverage",
    "build/package/module.py",
    "dist/telegram_assist_bot.whl",
    "config/configuration.json",
    "config/configuration.local.json",
    "config/configuration.production.json",
    "config/production.secrets.json",
    "credentials.json",
    "deploy/client.p12",
    "deploy/client.pfx",
    "deploy/private.key",
    "deploy/private.pem",
    "deploy/signing.jks",
    ".ssh/id_rsa",
    ".ssh/id_rsa.backup",
    "secrets/telegram-token.txt",
    "runtime/admin.session",
    "runtime/admin.session-journal",
    "var/sessions/admin.session",
    "var/media/post.jpg",
    "media/post.jpg",
    "logs/application.log",
    "application.log",
)

_TRACKABLE_PATHS = (
    ".secrets.baseline",
    ".env.example",
    "config/configuration.example.json",
    "src/telegram_assist_bot/domain/__init__.py",
    "tests/fixtures/persian_utf8.json",
    "tests/fixtures/synthetic.key",
    "tests/fixtures/synthetic.session",
    "uv.lock",
)


def _is_ignored(repository_path: str) -> bool:
    """Return whether Git ignores a repository-relative path."""
    git_executable = shutil.which("git")
    if git_executable is None:
        raise AssertionError("git executable was not found")
    # Git is the system under test; the argument list is fixed and never uses a shell.
    completed = subprocess.run(  # noqa: S603
        [
            git_executable,
            "check-ignore",
            "--quiet",
            "--no-index",
            "--",
            repository_path,
        ],
        cwd=_REPOSITORY_ROOT,
        check=False,
        capture_output=True,
        encoding="utf-8",
        timeout=_GIT_TIMEOUT_SECONDS,
    )
    if completed.returncode not in {0, 1}:
        message = completed.stderr.strip() or "git check-ignore failed"
        raise AssertionError(message)
    return completed.returncode == 0


@pytest.mark.parametrize("repository_path", _IGNORED_PATHS)
def test_sensitive_or_generated_paths_are_ignored(repository_path: str) -> None:
    """Ensure local, sensitive, and generated paths cannot be added normally."""
    assert _is_ignored(repository_path)


@pytest.mark.parametrize("repository_path", _TRACKABLE_PATHS)
def test_examples_and_synthetic_fixtures_are_trackable(repository_path: str) -> None:
    """Ensure safe templates, source files, and synthetic fixtures stay trackable."""
    assert not _is_ignored(repository_path)
