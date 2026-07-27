"""Verify exact-boundary and shared-reference media cleanup."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.application.ports import MediaTransientError
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


def test_active_reference_defers_then_terminal_reference_allows_cleanup(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()

    async def scenario() -> None:
        async def chunk() -> AsyncIterator[bytes]:
            yield b"active"

        path, _, _ = await storage.store(MediaIdentity(-3, 1), chunk(), maximum_bytes=8)
        repository.media["active"] = media(MediaIdentity(-3, 1), path, now)
        repository.active_storage_paths.add(path)
        use_case = CleanupExpiredMedia(
            repository, storage, orphan_grace=timedelta(hours=1), batch_size=10
        )
        assert await use_case.execute(now=now) == 0
        assert await storage.exists(path)

        repository.active_storage_paths.remove(path)
        assert await use_case.execute(now=now) == 1
        assert not await storage.exists(path)

    asyncio.run(scenario())


def test_transient_delete_is_retried_and_bad_candidate_does_not_stop_batch(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)

    class FlakyStorage(LocalMediaStorage):
        attempts = 0

        async def delete(self, storage_path: str) -> bool:
            if storage_path.endswith("valid") and self.attempts == 0:
                self.attempts += 1
                raise MediaTransientError("synthetic transient")
            return await super().delete(storage_path)

    storage = FlakyStorage(tmp_path)
    repository = FakePreparationRepository()
    delays: list[float] = []
    failures: list[tuple[str | None, int]] = []

    async def scenario() -> None:
        async def chunk() -> AsyncIterator[bytes]:
            yield b"valid"

        path, _, _ = await storage.store(MediaIdentity(-4, 1), chunk(), maximum_bytes=8)
        # Use a stable suffix so the synthetic storage can identify this candidate.
        valid = tmp_path / "valid"
        (tmp_path / path).replace(valid)
        repository.media["bad"] = media(MediaIdentity(-4, 2), "../outside", now)
        repository.media["valid"] = media(MediaIdentity(-4, 1), "valid", now)

        async def sleeper(delay: float) -> None:
            delays.append(delay)

        result = await CleanupExpiredMedia(
            repository,
            storage,
            orphan_grace=timedelta(hours=1),
            batch_size=10,
            maximum_attempts=3,
            sleeper=sleeper,
            on_candidate_error=lambda item, _error, attempt: failures.append(
                (None if item is None else item.identity.key, attempt)
            ),
        ).execute(now=now)
        assert result == 1
        assert delays == [1.0]
        assert failures == [("-4_2_0", 1), ("-4_1_0", 1)]
        assert not valid.exists()

    asyncio.run(scenario())
