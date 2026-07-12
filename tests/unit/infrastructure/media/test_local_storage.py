"""Verify private atomic local media storage."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    MediaTooLargeError,
)
from telegram_assist_bot.domain.media import MediaIdentity
from telegram_assist_bot.infrastructure.media import LocalMediaStorage


async def chunks(*values: bytes) -> AsyncIterator[bytes]:
    """Yield synthetic byte chunks."""
    for value in values:
        yield value


def test_stream_commit_hash_idempotency_and_private_path(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "private")
    identity = MediaIdentity(-1001, 2)

    async def scenario() -> None:
        first = await storage.store(identity, chunks(b"abc", b"def"), maximum_bytes=10)
        second = await storage.store(identity, chunks(b"abcdef"), maximum_bytes=10)
        assert first == second
        assert first[0].startswith("sha256/")
        assert "abcdef" not in first[0]
        assert await storage.exists(first[0])

    asyncio.run(scenario())


def test_size_failure_removes_partial_and_rejects_escape(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "private")

    async def scenario() -> None:
        with pytest.raises(MediaTooLargeError):
            await storage.store(
                MediaIdentity(-1, 1), chunks(b"oversized"), maximum_bytes=2
            )
        assert not tuple((tmp_path / "private" / ".tmp").glob("*.partial"))
        with pytest.raises(MediaPermanentError):
            await storage.delete("../outside")
        with pytest.raises(MediaPermanentError):
            await storage.delete(str((tmp_path / "absolute").resolve()))

    asyncio.run(scenario())


def test_symlink_component_is_rejected_deterministically(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalMediaStorage(tmp_path / "private")
    original = Path.is_symlink

    def is_symlink(path: Path) -> bool:
        return path.name == "linked" or original(path)

    monkeypatch.setattr(Path, "is_symlink", is_symlink)
    with pytest.raises(MediaPermanentError, match="symbolic"):
        asyncio.run(storage.delete("linked/file"))


def test_delete_is_idempotent_and_stale_temp_is_bounded(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "private")

    async def scenario() -> None:
        path, _, _ = await storage.store(
            MediaIdentity(-1, 1), chunks(b"x"), maximum_bytes=2
        )
        assert await storage.delete(path)
        assert not await storage.delete(path)
        partial = tmp_path / "private" / ".tmp" / "old.partial"
        partial.write_bytes(b"x")
        assert (
            await storage.delete_stale_temporary_files(
                older_than=datetime.now(UTC) + timedelta(seconds=1), limit=1
            )
            == 1
        )

    asyncio.run(scenario())
