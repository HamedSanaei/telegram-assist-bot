"""Bounded idempotent cleanup of expired and orphaned private media."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        ContentPreparationRepository,
        MediaStorage,
    )


class CleanupExpiredMedia:
    """Recheck references before deleting a bounded cleanup batch."""

    def __init__(
        self,
        repository: ContentPreparationRepository,
        storage: MediaStorage,
        *,
        orphan_grace: timedelta,
        batch_size: int,
    ) -> None:
        """Initialize bounded cleanup policy and injected boundaries."""
        if orphan_grace <= timedelta(0) or not 1 <= batch_size <= 1000:
            raise ValueError("Media cleanup bounds are invalid.")
        self._repository = repository
        self._storage = storage
        self._grace = orphan_grace
        self._batch = batch_size

    async def execute(self, *, now: datetime) -> int:
        """Delete only expired, unreferenced candidates and stale temp files."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("Cleanup clock must be timezone-aware.")
        candidates = await self._repository.list_cleanup_candidates(
            now=now, orphan_before=now - self._grace, limit=self._batch
        )
        cleaned = 0
        for media in candidates:
            if await self._repository.is_storage_path_referenced(
                media.storage_path, now=now
            ):
                continue
            await self._storage.delete(media.storage_path)
            if await self._repository.mark_media_cleaned(
                media.identity, cleaned_at=now
            ):
                cleaned += 1
        cleaned += await self._storage.delete_stale_temporary_files(
            older_than=now - self._grace, limit=self._batch
        )
        return cleaned
