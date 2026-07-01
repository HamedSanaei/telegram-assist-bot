"""Use case: remove expired posts and stale queue items."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.domain.interfaces import PostRepository, QueueRepository
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class CleanupService:
    """
    Removes expired data.

    MongoDB already deletes expired post documents through its TTL index
    on ``expires_at``; this service acts as a safety net for the posts
    collection and additionally expires unfinished queue items older
    than the retention window.

    Example:
        service = CleanupService(posts, queue, retention_days=14)
        await service.run()
    """

    def __init__(
        self,
        posts: PostRepository,
        queue: QueueRepository,
        retention_days: int = 14,
    ) -> None:
        """
        Args:
            posts: Post repository.
            queue: Queue repository.
            retention_days: Retention window in days (default 14).
        """
        self._posts = posts
        self._queue = queue
        self._retention_days = retention_days

    async def run(self, now: datetime | None = None) -> tuple[int, int]:
        """
        Execute one cleanup pass.

        Args:
            now: Current UTC time; injectable for tests.

        Returns:
            Tuple of ``(deleted_posts, expired_queue_items)``.

        Raises:
            RepositoryError: When a persistence operation fails.
        """
        current = now or datetime.now(timezone.utc)
        deleted_posts = await self._posts.delete_expired(current)
        cutoff = current - timedelta(days=self._retention_days)
        expired_items = await self._queue.expire_older_than(cutoff)
        logger.info(
            "Cleanup finished deleted_posts=%d expired_queue_items=%d",
            deleted_posts,
            expired_items,
        )
        return deleted_posts, expired_items
