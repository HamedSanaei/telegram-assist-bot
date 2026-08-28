"""Verify private atomic local media storage."""

import asyncio
import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    MediaTooLargeError,
    MediaTransientError,
    OrphanMediaFile,
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


@pytest.mark.parametrize(
    ("magic", "expected"),
    [
        (b"\xff\xd8\xffbytes", "jpg"),
        (b"\x89PNG\r\n\x1a\nbytes", "png"),
        (b"RIFF0000WEBPbytes", "webp"),
        (b"GIF87abytes", "gif"),
        (b"%PDF-bytes", "pdf"),
        (b"PK\x03\x04bytes", "zip"),
        (b"\x00\x00\x00\x18ftypqt  bytes", "mov"),
        (b"\x00\x00\x00\x18ftypm4a bytes", "m4a"),
        (b"\x1aE\xdf\xa3bytes", "mkv"),
        (b"ID3bytes", "mp3"),
        (b"OggSbytes", "ogg"),
        (b"unknown", "bin"),
    ],
)
def test_preview_magic_detection_is_explicit(
    tmp_path: Path, magic: bytes, expected: str
) -> None:
    source = tmp_path / "media"
    source.write_bytes(magic)
    assert LocalMediaStorage._preview_extension(source, None, None) == expected


def test_preview_extension_prefers_supported_filename_after_unknown_mime(
    tmp_path: Path,
) -> None:
    source = tmp_path / "media"
    source.write_bytes(b"unknown")
    assert (
        LocalMediaStorage._preview_extension(
            source, "application/octet-stream", "ITEM.WEBP"
        )
        == "webp"
    )


def test_preview_default_root_stays_inside_managed_root(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "media", preview_enabled=True)

    async def scenario() -> None:
        path, size, content_hash = await storage.store(
            MediaIdentity(-1, 5), chunks(b"\xff\xd8\xffpreview"), maximum_bytes=100
        )
        media = StoredMedia(
            MediaIdentity(-1, 5),
            MediaType.PHOTO,
            content_hash,
            size,
            "image/jpeg",
            "image.jpeg",
            path,
            datetime.now(UTC) + timedelta(days=1),
        )
        assert await storage.ensure_preview(media)
        preview = tmp_path / "media" / ".preview" / f"{content_hash}.jpg"
        assert preview.is_file()
        # No uncontrolled container-path preview is created by default.
        assert not (tmp_path / "data" / "media-preview").exists()

    asyncio.run(scenario())


def test_preview_deletion_removes_only_owned_preview_files(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path, preview_enabled=True)

    async def scenario() -> None:
        path, size, content_hash = await storage.store(
            MediaIdentity(-1, 6), chunks(b"\xff\xd8\xffowned"), maximum_bytes=100
        )
        media = StoredMedia(
            MediaIdentity(-1, 6),
            MediaType.PHOTO,
            content_hash,
            size,
            "image/jpeg",
            "image.jpeg",
            path,
            datetime.now(UTC) + timedelta(days=1),
        )
        assert await storage.ensure_preview(media)
        preview = tmp_path / ".preview" / f"{content_hash}.jpg"
        assert preview.is_file()
        assert await storage.delete_previews(content_hash) == 1
        assert not preview.exists()
        assert await storage.delete_previews(content_hash) == 0

    asyncio.run(scenario())


def test_orphan_scan_only_returns_old_canonical_files(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "private")
    root = tmp_path / "private"
    old_stamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()

    async def scenario() -> None:
        await storage.store(MediaIdentity(-1, 1), chunks(b"tracked"), maximum_bytes=100)
        prefix_dir = root / "sha256" / "ab"
        prefix_dir.mkdir(parents=True, exist_ok=True)
        old_file = prefix_dir / ("c" * 64)
        old_file.write_bytes(b"old")
        os.utime(old_file, (old_stamp, old_stamp))
        fresh_file = prefix_dir / ("d" * 64)
        fresh_file.write_bytes(b"fresh")
        (prefix_dir / "not-a-hash").write_bytes(b"junk")
        (prefix_dir / ("e" * 40)).write_bytes(b"short")
        nested = root / "sha256" / "nothex"
        nested.mkdir()
        (nested / ("f" * 64)).write_bytes(b"nested")
        outside = root / "outside"
        outside.mkdir()
        (outside / ("g" * 64)).write_bytes(b"outside")
        boundary = datetime.now(UTC) - timedelta(hours=1)
        candidates = await storage.list_orphan_candidates(
            older_than=boundary, limit=100
        )
        assert candidates == (
            OrphanMediaFile(
                storage_path=f"sha256/ab/{'c' * 64}",
                content_hash="c" * 64,
                size_bytes=3,
            ),
        )

    asyncio.run(scenario())


def test_orphan_scan_rejects_symlink_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalMediaStorage(tmp_path / "private")
    root = tmp_path / "private"
    old_stamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()

    async def scenario() -> None:
        prefix_dir = root / "sha256" / "ab"
        prefix_dir.mkdir(parents=True, exist_ok=True)
        old_file = prefix_dir / ("c" * 64)
        old_file.write_bytes(b"old")
        os.utime(old_file, (old_stamp, old_stamp))
        original = Path.is_symlink

        def is_symlink(path: Path) -> bool:
            return path.name == "cd" or original(path)

        monkeypatch.setattr(Path, "is_symlink", is_symlink)
        escaped = root / "sha256" / "cd"
        escaped.mkdir()
        escaped_file = escaped / ("d" * 64)
        escaped_file.write_bytes(b"escape")
        os.utime(escaped_file, (old_stamp, old_stamp))
        boundary = datetime.now(UTC) - timedelta(hours=1)
        candidates = await storage.list_orphan_candidates(
            older_than=boundary, limit=100
        )
        assert candidates == (
            OrphanMediaFile(
                storage_path=f"sha256/ab/{'c' * 64}",
                content_hash="c" * 64,
                size_bytes=3,
            ),
        )
        assert escaped_file.exists()

    asyncio.run(scenario())


def test_orphan_scan_is_bounded_by_limit(tmp_path: Path) -> None:
    storage = LocalMediaStorage(tmp_path / "private")
    root = tmp_path / "private"
    old_stamp = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    prefix_dir = root / "sha256" / "ab"
    prefix_dir.mkdir(parents=True, exist_ok=True)
    for index in range(5):
        name = f"{index:02d}" + "0" * 62
        path = prefix_dir / name
        path.write_bytes(b"x")
        os.utime(path, (old_stamp, old_stamp))

    async def scenario() -> None:
        boundary = datetime.now(UTC) - timedelta(hours=1)
        candidates = await storage.list_orphan_candidates(older_than=boundary, limit=2)
        assert len(candidates) == 2
        assert candidates[0].storage_path.startswith("sha256/ab/")

    asyncio.run(scenario())


def test_store_rejects_invalid_limits_chunks_and_hash_collision(
    tmp_path: Path,
) -> None:
    storage = LocalMediaStorage(tmp_path / "private")

    async def scenario() -> None:
        with pytest.raises(ValueError):  # noqa: PT011
            await storage.store(MediaIdentity(-1, 1), chunks(b"x"), maximum_bytes=0)
        with pytest.raises(MediaPermanentError, match="invalid chunk"):
            await storage.store(MediaIdentity(-1, 2), chunks(b""), maximum_bytes=1)
        relative, _, _ = await storage.store(
            MediaIdentity(-1, 3), chunks(b"same"), maximum_bytes=10
        )
        (tmp_path / "private" / relative).write_bytes(b"wrong")
        with pytest.raises(MediaPermanentError, match="inconsistent"):
            await storage.store(MediaIdentity(-1, 4), chunks(b"same"), maximum_bytes=10)

    asyncio.run(scenario())


def test_delete_rejects_directory_and_maps_os_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = LocalMediaStorage(tmp_path / "private")
    (tmp_path / "private" / "directory").mkdir()

    async def scenario() -> None:
        with pytest.raises(MediaPermanentError, match="regular"):
            await storage.delete("directory")
        path, _, _ = await storage.store(
            MediaIdentity(-1, 9), chunks(b"x"), maximum_bytes=2
        )

        def fail_unlink(*_args: object, **_kwargs: object) -> None:
            raise OSError

        monkeypatch.setattr(Path, "unlink", fail_unlink)
        with pytest.raises(MediaTransientError):
            await storage.delete(path)

    asyncio.run(scenario())
