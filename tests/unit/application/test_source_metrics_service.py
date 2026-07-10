"""Unit tests for collector-session source metric refresh."""

from __future__ import annotations

from src.application.source_metrics_service import SourceMetricsService
from src.domain.entities import Post, PostSourceMetrics
from src.domain.enums import QueueItemType, SourceMetricsStatus
from tests.unit.application.fakes import (
    FakeMetadataRefresher,
    FakePostRepository,
    FakeQueueRepository,
)


async def test_refreshes_metrics_then_enqueues_quality_score() -> None:
    """A collector refresh hands the post to the main scoring worker."""
    posts = FakePostRepository()
    queue = FakeQueueRepository()
    await posts.save(
        Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=10,
            text="خبر",
            content_hash="hash",
        )
    )
    refresher = FakeMetadataRefresher(PostSourceMetrics(views=200, forwards=12))

    await SourceMetricsService(posts, queue, refresher).refresh_post("p1")

    assert refresher.calls == [(-100, 10)]
    assert posts.posts["p1"].source_metrics.views == 200
    assert posts.posts["p1"].source_metrics_status == SourceMetricsStatus.REFRESHED
    assert [item.type for item in queue.items] == [QueueItemType.QUALITY_SCORE_UPDATE]


async def test_unavailable_metrics_still_enqueue_quality_score() -> None:
    """Telegram access failure never hides an already approved post."""
    posts = FakePostRepository()
    queue = FakeQueueRepository()
    await posts.save(
        Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=10,
            text="خبر",
            content_hash="hash",
            source_metrics=PostSourceMetrics(views=3),
        )
    )

    await SourceMetricsService(
        posts, queue, FakeMetadataRefresher(None)
    ).refresh_post("p1")

    assert posts.posts["p1"].source_metrics_status == SourceMetricsStatus.UNAVAILABLE
    assert posts.posts["p1"].source_metrics.views == 3
    assert [item.type for item in queue.items] == [QueueItemType.QUALITY_SCORE_UPDATE]
