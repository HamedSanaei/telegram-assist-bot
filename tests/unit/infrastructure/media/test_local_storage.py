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
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.shared.config.models import MediaStorageConfig


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


def test_preview_configuration_defaults_to_disabled_and_accepts_true() -> None:
    assert MediaStorageConfig().preview_enabled is False
    assert MediaStorageConfig.model_validate({"preview_enabled": True}).preview_enabled


def test_preview_copy_uses_mime_extension_and_reuses_existing(tmp_path: Path) -> None:
    canonical_root = tmp_path / "data" / "media"
    preview_root = tmp_path / "data" / "media-preview"
    storage = LocalMediaStorage(
        canonical_root, preview_enabled=True, preview_root=preview_root
    )
    original = b"\xff\xd8\xffjpeg-preview-bytes"

    async def scenario() -> None:
        path, size, content_hash = await storage.store(
            MediaIdentity(-1, 1), chunks(original), maximum_bytes=100
        )
        media = StoredMedia(
            MediaIdentity(-1, 1),
            MediaType.PHOTO,
            content_hash,
            size,
            "image/jpeg",
            "image.jpeg",
            path,
            datetime.now(UTC) + timedelta(days=1),
        )
        assert await storage.ensure_preview(media)
        preview = preview_root / f"{content_hash}.jpg"
        canonical = canonical_root / path
        assert preview.read_bytes() == original
        assert canonical.read_bytes() == original
        assert not preview.is_symlink()
        assert not await storage.ensure_preview(media)

    asyncio.run(scenario())


def test_preview_uses_mp4_magic_and_backfills_only_missing_files(
    tmp_path: Path,
) -> None:
    canonical_root = tmp_path / "data" / "media"
    preview_root = tmp_path / "data" / "media-preview"
    storage = LocalMediaStorage(
        canonical_root, preview_enabled=True, preview_root=preview_root
    )
    original = b"\x00\x00\x00\x18ftypisomvideo-preview-bytes"

    async def scenario() -> None:
        path, size, content_hash = await storage.store(
            MediaIdentity(-1, 2), chunks(original), maximum_bytes=100
        )
        media = StoredMedia(
            MediaIdentity(-1, 2),
            MediaType.VIDEO,
            content_hash,
            size,
            None,
            None,
            path,
            datetime.now(UTC) + timedelta(days=1),
        )
        assert await storage.backfill_previews((media,)) == 1
        preview = preview_root / f"{content_hash}.mp4"
        assert preview.read_bytes() == original
        assert await storage.backfill_previews((media,)) == 0

    asyncio.run(scenario())


def test_disabled_preview_never_creates_preview_directory(tmp_path: Path) -> None:
    preview_root = tmp_path / "data" / "media-preview"
    storage = LocalMediaStorage(
        tmp_path / "data" / "media", preview_enabled=False, preview_root=preview_root
    )

    async def scenario() -> None:
        path, size, content_hash = await storage.store(
            MediaIdentity(-1, 3), chunks(b"bytes"), maximum_bytes=100
        )
        media = StoredMedia(
            MediaIdentity(-1, 3),
            MediaType.DOCUMENT,
            content_hash,
            size,
            "application/pdf",
            "item.pdf",
            path,
            datetime.now(UTC) + timedelta(days=1),
        )
        assert not await storage.prepare_preview_directory()
        assert not await storage.ensure_preview(media)
        assert not preview_root.exists()

    asyncio.run(scenario())
