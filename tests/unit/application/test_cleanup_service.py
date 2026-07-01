"""Unit tests for the 14-day expiration cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from src.application.cleanup_service import CleanupService
from src.domain.entities import Post
from src.domain.enums import QueueItemType
from tests.unit.application.fakes import FakePostRepository, FakeQueueRepository


def _post(post_id: str, expires_at: datetime) -> Post:
    """Build a post with a specific expiry."""
    return Post(
        post_id=post_id,
        source_chat_id=-1,
        source_message_id=1,
        text="متن",
        content_hash=post_id,
        expires_at=expires_at,
    )


class TestCleanupService:
    """Tests for :class:`CleanupService`."""

    async def test_removes_only_expired_posts(self) -> None:
        now = datetime.now(timezone.utc)
        posts = FakePostRepository()
        await posts.save(_post("old", now - timedelta(days=1)))
        await posts.save(_post("fresh", now + timedelta(days=13)))
        queue = FakeQueueRepository()
        service = CleanupService(posts, queue, retention_days=14)
        deleted, _ = await service.run(now)
        assert deleted == 1
        assert "fresh" in posts.posts
        assert "old" not in posts.posts

    async def test_expires_stale_queue_items(self) -> None:
        now = datetime.now(timezone.utc)
        posts = FakePostRepository()
        queue = FakeQueueRepository()
        await queue.enqueue(
            QueueItemType.APPROVAL_REQUEST,
            {"post_id": "x"},
            scheduled_at=now - timedelta(days=20),
        )
        service = CleanupService(posts, queue, retention_days=14)
        _, expired = await service.run(now)
        assert expired == 1
