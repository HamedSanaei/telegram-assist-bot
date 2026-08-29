"""Bounded idempotent cleanup of expired and orphaned private media."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import (
    MediaOperationError,
    MediaPermanentError,
    MediaTransientError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from telegram_assist_bot.application.ports import (
        ContentPreparationRepository,
        MediaStorage,
    )
    from telegram_assist_bot.domain.media import StoredMedia
    from telegram_assist_bot.shared.retry.executor import AsyncSleeper


@dataclass(frozen=True, slots=True)
class CleanupBatchResult:
    """Observe one bounded cleanup batch without exposing file contents."""

    scanned: int = 0
    deleted: int = 0
    deferred: int = 0
    orphan_scanned: int = 0
    orphan_deleted: int = 0
    temporary_deleted: int = 0
    failed: int = 0
    deleted_bytes: int = 0
    orphan_deleted_bytes: int = 0
    more_eligible_work: bool = False


@dataclass(slots=True)
class _BatchCounters:
    """Accumulate one batch result through bounded candidate processing."""

    scanned: int = 0
    deleted: int = 0
    deferred: int = 0
    orphan_scanned: int = 0
    orphan_deleted: int = 0
    temporary_deleted: int = 0
    failed: int = 0
    deleted_bytes: int = 0
    orphan_deleted_bytes: int = 0


class CleanupExpiredMedia:
    """Recheck references before deleting a bounded cleanup batch."""

    def __init__(
        self,
        repository: ContentPreparationRepository,
        storage: MediaStorage,
        *,
        orphan_grace: timedelta,
        batch_size: int,
        defer_interval: timedelta,
        maximum_attempts: int = 3,
        sleeper: AsyncSleeper = asyncio.sleep,
        on_candidate_error: Callable[
            [StoredMedia | None, MediaOperationError, int], None
        ]
        | None = None,
    ) -> None:
        """Initialize bounded cleanup policy and injected boundaries."""
        if (
            orphan_grace <= timedelta(0)
            or not 1 <= batch_size <= 1000
            or not timedelta(seconds=1) <= defer_interval <= timedelta(days=7)
            or not 1 <= maximum_attempts <= 10
        ):
            raise ValueError("Media cleanup bounds are invalid.")
        self._repository = repository
        self._storage = storage
        self._grace = orphan_grace
        self._batch = batch_size
        self._defer_interval = defer_interval
        self._maximum_attempts = maximum_attempts
        self._sleeper = sleeper
        self._on_candidate_error = on_candidate_error

    async def execute(self, *, now: datetime) -> CleanupBatchResult:
        """Delete only expired, unreferenced candidates and stale orphans."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Cleanup clock must be timezone-aware.")
        counters = _BatchCounters()
        candidates = await self._repository.list_cleanup_candidates(
            now=now, orphan_before=now - self._grace, limit=self._batch
        )
        counters.scanned = len(candidates)
        for media in candidates:
            await self._cleanup_candidate(media, now=now, counters=counters)
        orphan_budget = max(0, self._batch - len(candidates))
        if orphan_budget:
            await self._cleanup_orphans(now=now, limit=orphan_budget, counters=counters)
        temp_budget = max(0, orphan_budget - counters.orphan_scanned)
        if temp_budget:
            try:
                counters.temporary_deleted += (
                    await self._storage.delete_stale_temporary_files(
                        older_than=now - self._grace, limit=temp_budget
                    )
                )
            except MediaOperationError as error:
                self._report_candidate_error(None, error, attempt=1)
        more_eligible_work = counters.scanned == self._batch or (
            orphan_budget > 0 and counters.orphan_scanned == orphan_budget
        )
        return CleanupBatchResult(
            scanned=counters.scanned,
            deleted=counters.deleted,
            deferred=counters.deferred,
            orphan_scanned=counters.orphan_scanned,
            orphan_deleted=counters.orphan_deleted,
            temporary_deleted=counters.temporary_deleted,
            failed=counters.failed,
            deleted_bytes=counters.deleted_bytes,
            orphan_deleted_bytes=counters.orphan_deleted_bytes,
            more_eligible_work=more_eligible_work,
        )

    async def _cleanup_candidate(
        self, media: StoredMedia, *, now: datetime, counters: _BatchCounters
    ) -> None:
        """Clean, defer, or fail one tracked candidate without blocking others."""
        if await self._repository.is_storage_path_referenced(
            media.storage_path, now=now
        ):
            await self._defer_media(media, now=now)
            counters.deferred += 1
            return
        for attempt in range(1, self._maximum_attempts + 1):
            try:
                # Recheck immediately before each filesystem attempt.
                if await self._repository.is_storage_path_referenced(
                    media.storage_path, now=now
                ):
                    await self._defer_media(media, now=now)
                    counters.deferred += 1
                    return
                deleted = await self._storage.delete(media.storage_path)
                if not await self._repository.mark_media_cleaned(
                    media.identity, cleaned_at=now
                ):
                    # A competing worker already completed this identity.
                    return
                counters.deleted += 1
                if deleted:
                    counters.deleted_bytes += media.size_bytes
                await self._delete_previews(media.content_hash)
                return
            except MediaPermanentError as error:
                self._report_candidate_error(media, error, attempt=attempt)
                await self._defer_media(media, now=now)
                counters.failed += 1
                return
            except MediaTransientError as error:
                self._report_candidate_error(media, error, attempt=attempt)
                if attempt == self._maximum_attempts:
                    await self._defer_media(media, now=now)
                    counters.failed += 1
                    return
                await self._sleeper(float(attempt))

    async def _cleanup_orphans(
        self, *, now: datetime, limit: int, counters: _BatchCounters
    ) -> None:
        """Delete bounded canonical files that lost every media record."""
        try:
            candidates = await self._storage.list_orphan_candidates(
                older_than=now - self._grace, limit=limit
            )
        except MediaOperationError as error:
            self._report_candidate_error(None, error, attempt=1)
            return
        counters.orphan_scanned = len(candidates)
        for candidate in candidates:
            if await self._repository.has_media_record_for_storage_path(
                candidate.storage_path
            ):
                continue
            if await self._repository.is_storage_path_referenced(
                candidate.storage_path, now=now
            ):
                continue
            try:
                deleted = await self._storage.delete(candidate.storage_path)
                if deleted:
                    counters.orphan_deleted += 1
                    counters.orphan_deleted_bytes += candidate.size_bytes
                await self._delete_previews(candidate.content_hash)
            except MediaPermanentError as error:
                self._report_candidate_error(None, error, attempt=1)
                counters.failed += 1
            except MediaTransientError as error:
                self._report_candidate_error(None, error, attempt=1)
                counters.failed += 1

    async def _defer_media(self, media: StoredMedia, *, now: datetime) -> None:
        """Push one busy or failing candidate to a bounded future attempt."""
        await self._repository.defer_media_cleanup(
            media.identity, until=now + self._defer_interval
        )

    async def _delete_previews(self, content_hash: str) -> None:
        """Remove generated previews that only this canonical file owns."""
        deleter = getattr(self._storage, "delete_previews", None)
        if deleter is not None:
            await deleter(content_hash)

    def _report_candidate_error(
        self,
        media: StoredMedia | None,
        error: MediaOperationError,
        *,
        attempt: int,
    ) -> None:
        """Report an isolated candidate failure without adding an outer dependency."""
        if self._on_candidate_error is not None:
            self._on_candidate_error(media, error, attempt)


__all__ = ("CleanupBatchResult", "CleanupExpiredMedia")
