"""Application use case for exact duplicate detection in a bounded window."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.text_normalization import (
    CONTENT_HASH_VERSION,
    NORMALIZATION_VERSION,
    exact_content_hash,
)
from telegram_assist_bot.domain.duplicates import DuplicateCheckResult

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import ContentPreparationRepository
    from telegram_assist_bot.domain.posts import PostId


class DetectExactDuplicate:
    """Compute and conditionally persist one canonical exact-duplicate result."""

    def __init__(self, repository: ContentPreparationRepository) -> None:
        """Initialize the application-owned persistence boundary."""
        self._repository = repository

    async def execute(
        self,
        *,
        post_id: PostId,
        text: str | None,
        caption: str | None,
        media_hashes: tuple[str, ...],
        checked_at: datetime,
    ) -> DuplicateCheckResult:
        """Check only the preceding fourteen-day non-expired window."""
        content_hash = exact_content_hash(
            text=text, caption=caption, media_hashes=media_hashes
        )
        match = await self._repository.find_duplicate(
            content_hash=content_hash,
            post_id=post_id,
            since=checked_at - timedelta(days=14),
        )
        result = DuplicateCheckResult(
            is_duplicate=match is not None,
            matched_post_id=match,
            method="ExactContentHash",
            normalization_version=NORMALIZATION_VERSION,
            hash_version=CONTENT_HASH_VERSION,
            content_hash=content_hash,
            checked_at=checked_at,
        )
        return await self._repository.save_duplicate_result(post_id, result)
