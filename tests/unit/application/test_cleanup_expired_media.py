"""Verify starvation-free, orphan-aware and preview-safe media cleanup."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    MediaStorage,
    MediaTransientError,
    OrphanMediaFile,
)
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from tests.unit.application.m2_fakes import FakePreparationRepository

_DEFER = timedelta(hours=1)


def media(identity: MediaIdentity, path: str, expires_at: datetime) -> StoredMedia:
    """Build synthetic valid media metadata."""
    return StoredMedia(
        identity, MediaType.PHOTO, "a" * 64, 1, None, None, path, expires_at
    )


def use_case(
    repository: FakePreparationRepository,
    storage: LocalMediaStorage,
    *,
    batch_size: int = 10,
) -> CleanupExpiredMedia:
    """Build one deterministic test cleanup boundary."""
    return CleanupExpiredMedia(
        repository,
        storage,
        orphan_grace=timedelta(hours=1),
        batch_size=batch_size,
        defer_interval=_DEFER,
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
        first = await use_case(repository, storage).execute(now=now)
        assert first.deleted == 0
        assert await storage.exists(path)
        repository.media.pop("fresh")
        second = await use_case(repository, storage).execute(now=now)
        assert second.deleted == 1
        assert not await storage.exists(path)

    asyncio.run(scenario())


def test_missing_file_and_two_workers_are_idempotent(tmp_path: Path) -> None:
    now = datetime(2026, 7, 12, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()
    repository.media["missing"] = media(MediaIdentity(-2, 1), "sha256/aa/missing", now)

    async def scenario() -> None:
        cleanup = use_case(repository, storage)
        outcomes = await asyncio.gather(
            cleanup.execute(now=now), cleanup.execute(now=now)
        )
        assert sum(item.deleted for item in outcomes) == 1
        assert (await use_case(repository, storage).execute(now=now)).deleted == 0

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
        cleanup = use_case(repository, storage)
        first = await cleanup.execute(now=now)
        assert first.deleted == 0
        assert first.deferred == 1
        assert await storage.exists(path)

        repository.active_storage_paths.remove(path)
        second = await cleanup.execute(now=now)
        assert second.deleted == 1
        assert not await storage.exists(path)

    asyncio.run(scenario())


def test_referenced_first_page_cannot_starve_later_unreferenced_candidate(
    tmp_path: Path,
) -> None:
    """The mandatory starvation regression: page 1 never blocks record 101."""
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()
    batch_size = 5
    for index in range(1, batch_size + 1):
        path = f"sha256/aa/{'a' * 64}"
        identity = MediaIdentity(-10, 1000 + index)
        repository.media[identity.key] = media(identity, path, now)
        repository.active_storage_paths.add(path)
    free_identity = MediaIdentity(-10, 2000)
    free_path, _, _ = asyncio.run(_store_real(storage, free_identity, b"free"))
    repository.media[free_identity.key] = media(free_identity, free_path, now)

    async def scenario() -> None:
        cleanup = use_case(repository, storage, batch_size=batch_size)
        first = await cleanup.execute(now=now)
        assert first.deleted == 0
        assert first.deferred == batch_size
        second = await cleanup.execute(now=now)
        assert second.deleted == 1
        assert second.deferred == 0
        assert not await storage.exists(free_path)
        assert free_identity.key in repository.cleaned

    asyncio.run(scenario())


async def _store_real(
    storage: LocalMediaStorage, identity: MediaIdentity, content: bytes
) -> tuple[str, int, str]:
    async def chunks() -> AsyncIterator[bytes]:
        yield content

    return await storage.store(identity, chunks(), maximum_bytes=len(content) + 1)


def test_deferred_candidates_become_eligible_again_after_defer_interval(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()
    identity = MediaIdentity(-11, 1)
    path = f"sha256/bb/{'b' * 64}"
    repository.media[identity.key] = media(identity, path, now)
    repository.active_storage_paths.add(path)

    async def scenario() -> None:
        cleanup = use_case(repository, storage, batch_size=2)
        assert (await cleanup.execute(now=now)).deferred == 1
        later = now + _DEFER + timedelta(seconds=1)
        repository.active_storage_paths.clear()
        assert (await cleanup.execute(now=later)).deleted == 1
        assert identity.key in repository.cleaned

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
            defer_interval=_DEFER,
            maximum_attempts=3,
            sleeper=sleeper,
            on_candidate_error=lambda item, _error, attempt: failures.append(
                (None if item is None else item.identity.key, attempt)
            ),
        ).execute(now=now)
        assert result.deleted == 1
        assert delays == [1.0]
        assert failures == [("-4_2_0", 1), ("-4_1_0", 1)]
        assert not valid.exists()
        assert result.failed == 1
        assert result.deferred == 0

    asyncio.run(scenario())


def test_orphan_file_deleted_only_when_old_and_truly_unreferenced(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()

    async def scenario() -> None:
        old_path, _, _ = await _store_real(
            storage, MediaIdentity(-20, 1), b"old-orphan"
        )
        old_file = tmp_path / old_path
        old_stamp = (now - timedelta(hours=2)).timestamp()
        import os

        os.utime(old_file, (old_stamp, old_stamp))
        recent_path, _, _ = await _store_real(storage, MediaIdentity(-20, 2), b"recent")
        tracked_path, _, _ = await _store_real(
            storage, MediaIdentity(-20, 3), b"tracked-orphan"
        )
        tracked_stamp = (now - timedelta(hours=2)).timestamp()
        os.utime(tmp_path / tracked_path, (tracked_stamp, tracked_stamp))
        tracked_identity = MediaIdentity(-20, 3)
        repository.media[tracked_identity.key] = media(
            tracked_identity, tracked_path, now + timedelta(days=1)
        )

        result = await use_case(repository, storage, batch_size=10).execute(now=now)
        assert result.orphan_scanned == 2
        assert result.orphan_deleted == 1
        assert not (tmp_path / old_path).exists()
        assert (tmp_path / recent_path).exists()
        assert (tmp_path / tracked_path).exists()

    asyncio.run(scenario())


def test_orphan_with_active_reference_is_preserved(tmp_path: Path) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()

    async def scenario() -> None:
        path, _, _ = await _store_real(
            storage, MediaIdentity(-21, 1), b"referenced-orphan"
        )
        old_stamp = (now - timedelta(hours=2)).timestamp()
        import os

        os.utime(tmp_path / path, (old_stamp, old_stamp))
        repository.active_storage_paths.add(path)

        result = await use_case(repository, storage, batch_size=10).execute(now=now)
        assert result.orphan_deleted == 0
        assert (tmp_path / path).exists()

    asyncio.run(scenario())


def test_preview_follows_canonical_cleanup_and_stays_inside_root(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path, preview_enabled=True)
    repository = FakePreparationRepository()

    async def scenario() -> None:
        path, size, content_hash = await _store_real(
            storage, MediaIdentity(-30, 1), b"\xff\xd8\xffpreview-bytes"
        )
        repository.media["expired"] = StoredMedia(
            MediaIdentity(-30, 1),
            MediaType.PHOTO,
            content_hash,
            size,
            "image/jpeg",
            "image.jpg",
            path,
            now,
        )
        stored = repository.media["expired"]
        assert await storage.ensure_preview(stored)
        preview = tmp_path / ".preview" / f"{content_hash}.jpg"
        assert preview.is_file()
        assert preview.is_relative_to(tmp_path)

        result = await use_case(repository, storage).execute(now=now)
        assert result.deleted == 1
        assert not preview.exists()
        assert not await storage.exists(path)

    asyncio.run(scenario())


def test_shared_content_path_deleted_exactly_once_after_last_reference(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()

    async def scenario() -> None:
        path, _, content_hash = await _store_real(
            storage, MediaIdentity(-40, 1), b"shared-bytes"
        )
        repository.media["expired"] = StoredMedia(
            MediaIdentity(-40, 1),
            MediaType.PHOTO,
            content_hash,
            11,
            None,
            None,
            path,
            now,
        )
        repository.media["active"] = StoredMedia(
            MediaIdentity(-40, 2),
            MediaType.PHOTO,
            content_hash,
            11,
            None,
            None,
            path,
            now + timedelta(days=1),
        )
        cleanup = use_case(repository, storage)
        assert (await cleanup.execute(now=now)).deleted == 0
        assert await storage.exists(path)
        repository.media.pop("active")
        assert (await cleanup.execute(now=now)).deleted == 1
        assert not await storage.exists(path)

    asyncio.run(scenario())


class DeferredMidwayRepository(FakePreparationRepository):
    """Simulate a reference appearing after the first eligibility check."""

    def __init__(self) -> None:
        super().__init__()
        self.checks = 0

    async def is_storage_path_referenced(
        self, storage_path: str, *, now: datetime
    ) -> bool:
        self.checks += 1
        return self.checks > 1


class AlwaysTransientStorage(LocalMediaStorage):
    """Simulate a storage whose delete never succeeds."""

    async def delete(self, storage_path: str) -> bool:
        raise MediaTransientError("synthetic persistent transient")


class AlreadyCleanedRepository(FakePreparationRepository):
    """Simulate a competing worker that already cleaned every candidate."""

    async def mark_media_cleaned(
        self, identity: MediaIdentity, *, cleaned_at: datetime
    ) -> bool:
        return False


class OrphanScanFailingStorage(LocalMediaStorage):
    """Simulate a bounded orphan scan failure."""

    async def list_orphan_candidates(
        self, *, older_than: datetime, limit: int
    ) -> tuple[OrphanMediaFile, ...]:
        raise MediaTransientError("synthetic orphan scan failure")


class OrphanDeleteFailingStorage(LocalMediaStorage):
    """Simulate a permanent failure deleting one canonical orphan."""

    async def delete(self, storage_path: str) -> bool:
        raise MediaPermanentError("synthetic orphan delete failure")


def test_reference_appearing_mid_batch_defers_instead_of_starving(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = DeferredMidwayRepository()
    identity = MediaIdentity(-60, 1)
    repository.media[identity.key] = media(identity, f"sha256/dd/{'d' * 64}", now)

    async def scenario() -> None:
        result = await use_case(repository, storage).execute(now=now)
        assert result.deleted == 0
        assert result.deferred == 1
        assert repository.checks == 2
        assert identity.key in repository.cleanup_next_check

    asyncio.run(scenario())


def test_persistent_transient_failure_defers_and_counts_failed(tmp_path: Path) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = AlwaysTransientStorage(tmp_path)
    repository = FakePreparationRepository()
    identity = MediaIdentity(-61, 1)
    repository.media[identity.key] = media(identity, f"sha256/ee/{'e' * 64}", now)
    delays: list[float] = []

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    async def scenario() -> None:
        cleanup = CleanupExpiredMedia(
            repository,
            storage,
            orphan_grace=timedelta(hours=1),
            batch_size=10,
            defer_interval=_DEFER,
            maximum_attempts=3,
            sleeper=sleeper,
        )
        result = await cleanup.execute(now=now)
        assert result.failed == 1
        assert result.deleted == 0
        assert delays == [1.0, 2.0]
        assert identity.key in repository.cleanup_next_check

    asyncio.run(scenario())


def test_competing_worker_cleaned_identity_is_not_double_counted(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = AlreadyCleanedRepository()
    identity = MediaIdentity(-62, 1)
    repository.media[identity.key] = media(identity, f"sha256/ff/{'f' * 64}", now)

    async def scenario() -> None:
        result = await use_case(repository, storage).execute(now=now)
        assert result.deleted == 0
        assert result.scanned == 1

    asyncio.run(scenario())


def test_orphan_scan_failure_is_isolated_and_reported(tmp_path: Path) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = OrphanScanFailingStorage(tmp_path)
    repository = FakePreparationRepository()
    failures: list[tuple[str | None, int]] = []

    async def scenario() -> None:
        cleanup = CleanupExpiredMedia(
            repository,
            storage,
            orphan_grace=timedelta(hours=1),
            batch_size=10,
            defer_interval=_DEFER,
            on_candidate_error=lambda item, _error, attempt: failures.append(
                (None if item is None else item.identity.key, attempt)
            ),
        )
        result = await cleanup.execute(now=now)
        assert result.orphan_scanned == 0
        assert result.orphan_deleted == 0
        assert failures == [(None, 1)]

    asyncio.run(scenario())


def test_orphan_delete_failure_counts_failed_without_aborting(tmp_path: Path) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = OrphanDeleteFailingStorage(tmp_path)
    repository = FakePreparationRepository()
    prefix_dir = tmp_path / "sha256" / "aa"
    prefix_dir.mkdir(parents=True, exist_ok=True)
    old_file = prefix_dir / ("a" * 64)
    old_file.write_bytes(b"x")
    old_stamp = (now - timedelta(hours=2)).timestamp()
    import os

    os.utime(old_file, (old_stamp, old_stamp))

    async def scenario() -> None:
        result = await use_case(repository, storage).execute(now=now)
        assert result.orphan_scanned == 1
        assert result.orphan_deleted == 0
        assert result.failed == 1

    asyncio.run(scenario())


def test_cleanup_without_preview_support_skips_preview_deletion(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    repository = FakePreparationRepository()

    class MinimalStorage:
        """Implement the storage port without the optional preview extension."""

        async def delete(self, storage_path: str) -> bool:
            return True

        async def list_orphan_candidates(
            self, *, older_than: datetime, limit: int
        ) -> tuple[OrphanMediaFile, ...]:
            return ()

        async def delete_stale_temporary_files(
            self, *, older_than: datetime, limit: int
        ) -> int:
            return 0

    identity = MediaIdentity(-63, 1)
    repository.media[identity.key] = media(identity, f"sha256/gg/{'g' * 64}", now)

    async def scenario() -> None:
        result = await CleanupExpiredMedia(
            repository,
            cast("MediaStorage", MinimalStorage()),
            orphan_grace=timedelta(hours=1),
            batch_size=10,
            defer_interval=_DEFER,
        ).execute(now=now)
        assert result.deleted == 1

    asyncio.run(scenario())


def test_batch_result_reports_more_eligible_work_while_backlog_remains(
    tmp_path: Path,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()
    for index in range(1, 7):
        identity = MediaIdentity(-50, index)
        repository.media[identity.key] = media(identity, f"sha256/cc/{'c' * 64}", now)

    async def scenario() -> None:
        cleanup = use_case(repository, storage, batch_size=4)
        first = await cleanup.execute(now=now)
        assert first.deleted == 4
        assert first.more_eligible_work
        second = await cleanup.execute(now=now)
        assert second.deleted == 2
        assert not second.more_eligible_work

    asyncio.run(scenario())
