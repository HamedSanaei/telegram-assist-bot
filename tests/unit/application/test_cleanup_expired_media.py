"""Verify exact-boundary and shared-reference media cleanup."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from tests.unit.application.m2_fakes import FakePreparationRepository


def media(identity: MediaIdentity, path: str, expires_at: datetime) -> StoredMedia:
    """Build synthetic valid media metadata."""
    return StoredMedia(
        identity, MediaType.PHOTO, "a" * 64, 1, None, None, path, expires_at
    )


def test_expired_deleted_fresh_shared_preserved(tmp_path: Path) -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()

    async def scenario() -> None:
        async def chunk() -> AsyncIterator[bytes]:
            yield b"x"

        path, _, _ = await storage.store(MediaIdentity(-1, 1), chunk(), maximum_bytes=2)
        repository.media["expired"] = media(MediaIdentity(-1, 1), path, now)
        repository.media["fresh"] = media(
            MediaIdentity(-1, 2), path, now + timedelta(days=1)
        )
        cleaned = await CleanupExpiredMedia(
            repository, storage, orphan_grace=timedelta(hours=1), batch_size=10
        ).execute(now=now)
        assert cleaned == 0
        assert await storage.exists(path)
        repository.media.pop("fresh")
        assert (
            await CleanupExpiredMedia(
                repository, storage, orphan_grace=timedelta(hours=1), batch_size=10
            ).execute(now=now)
            == 1
        )
        assert not await storage.exists(path)

    asyncio.run(scenario())


def test_missing_file_and_two_workers_are_idempotent(tmp_path: Path) -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()
    repository.media["missing"] = media(MediaIdentity(-2, 1), "sha256/aa/missing", now)

    async def scenario() -> None:
        use_case = CleanupExpiredMedia(
            repository,
            storage,
            orphan_grace=timedelta(hours=1),
            batch_size=10,
        )
        outcomes = await asyncio.gather(
            use_case.execute(now=now), use_case.execute(now=now)
        )
        assert sum(outcomes) == 1
        assert await use_case.execute(now=now) == 0

    asyncio.run(scenario())
