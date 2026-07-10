"""Refresh delayed Telegram source metrics through the collector session."""

from __future__ import annotations

from src.domain.enums import QualityScoreStatus, QueueItemType, SourceMetricsStatus
from src.domain.interfaces import PostRepository, QueueRepository, SourceMetadataRefresher
from src.shared.errors import ApprovalStateError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class SourceMetricsService:
    """Refresh one post's source metrics and hand it to quality scoring."""

    def __init__(
        self,
        posts: PostRepository,
        queue: QueueRepository,
        refresher: SourceMetadataRefresher,
    ) -> None:
        """Initialize the service with persistence, queue, and Telegram ports."""
        self._posts = posts
        self._queue = queue
        self._refresher = refresher

    async def refresh_post(self, post_id: str) -> None:
        """Refresh metrics once and enqueue idempotent quality-score work."""
        post = await self._posts.get(post_id)
        if post is None:
            raise ApprovalStateError(f"Post {post_id} not found for metrics refresh")
        if post.source_metrics_status == SourceMetricsStatus.PENDING:
            try:
                metrics = await self._refresher.refresh_metrics(
                    post.source_chat_id,
                    post.source_message_id,
                )
            except Exception as exc:
                metrics = None
                logger.warning(
                    "Source metrics refresh failed post=%s chat=%s msg=%s error=%s",
                    post_id,
                    post.source_chat_id,
                    post.source_message_id,
                    exc,
                )
            if metrics is None:
                post.source_metrics_status = SourceMetricsStatus.UNAVAILABLE
                logger.warning(
                    "Source metrics unavailable post=%s chat=%s msg=%s; using stored metrics",
                    post_id,
                    post.source_chat_id,
                    post.source_message_id,
                )
            else:
                post.source_metrics = metrics
                post.source_metrics_status = SourceMetricsStatus.REFRESHED
                logger.info(
                    "Source metrics refreshed post=%s views=%s forwards=%s reactions=%s replies=%s",
                    post_id,
                    metrics.views,
                    metrics.forwards,
                    metrics.reactions_count,
                    metrics.replies_count,
                )
            await self._posts.save(post)
        if post.quality_score_status != QualityScoreStatus.PENDING:
            return
        await self._queue.enqueue(
            QueueItemType.QUALITY_SCORE_UPDATE,
            {"post_id": post_id, "metrics_ready": True},
        )
        logger.info("Enqueued quality score after metrics refresh post=%s", post_id)
