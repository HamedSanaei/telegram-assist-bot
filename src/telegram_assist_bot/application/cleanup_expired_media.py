"""Bounded idempotent cleanup of expired and orphaned private media."""

from __future__ import annotations

import asyncio
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


class CleanupExpiredMedia:
    """Recheck references before deleting a bounded cleanup batch."""

    def __init__(
        self,
        repository: ContentPreparationRepository,
        storage: MediaStorage,
        *,
        orphan_grace: timedelta,
        batch_size: int,
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
            or not 1 <= maximum_attempts <= 10
        ):
            raise ValueError("Media cleanup bounds are invalid.")
        self._repository = repository
        self._storage = storage
        self._grace = orphan_grace
        self._batch = batch_size
        self._maximum_attempts = maximum_attempts
        self._sleeper = sleeper
        self._on_candidate_error = on_candidate_error

    async def execute(self, *, now: datetime) -> int:
        """Delete only expired, unreferenced candidates and stale temp files."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Cleanup clock must be timezone-aware.")
        candidates = await self._repository.list_cleanup_candidates(
            now=now, orphan_before=now - self._grace, limit=self._batch
        )
        cleaned = 0
        for media in candidates:
            if await self._cleanup_candidate(media, now=now):
                cleaned += 1
        remaining = max(0, self._batch - len(candidates))
        if remaining:
            try:
                cleaned += await self._storage.delete_stale_temporary_files(
                    older_than=now - self._grace, limit=remaining
                )
            except MediaOperationError as error:
                self._report_candidate_error(None, error, attempt=1)
        return cleaned

    async def _cleanup_candidate(self, media: StoredMedia, *, now: datetime) -> bool:
        """Isolate one candidate while preserving critical repository failures."""
        if await self._repository.is_storage_path_referenced(
            media.storage_path, now=now
        ):
            return False
        for attempt in range(1, self._maximum_attempts + 1):
            try:
                # Recheck immediately before each filesystem attempt.
                if await self._repository.is_storage_path_referenced(
                    media.storage_path, now=now
                ):
                    return False
                await self._storage.delete(media.storage_path)
                return await self._repository.mark_media_cleaned(
                    media.identity, cleaned_at=now
                )
            except MediaPermanentError as error:
                self._report_candidate_error(media, error, attempt=attempt)
                return False
            except MediaTransientError as error:
                self._report_candidate_error(media, error, attempt=attempt)
                if attempt == self._maximum_attempts:
                    return False
                await self._sleeper(float(attempt))
        return False

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
