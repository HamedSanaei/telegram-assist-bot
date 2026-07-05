"""Use case for delayed AI quality scoring of collected posts."""

from __future__ import annotations

from datetime import datetime, timezone

from src.application.ai_service import AiService
from src.domain.entities import Post, PostQualityScore
from src.domain.enums import QueueItemType
from src.domain.interfaces import PostRepository, SourceMetadataRefresher
from src.shared.errors import ApprovalStateError, QualityScoringError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class QualityScoreService:
    """
    Calculates and stores an AI repost-quality score for a collected post.

    The score is advisory only. If all AI providers fail, the post still
    continues through the pipeline with a neutral fallback score so admins
    do not lose content during temporary AI outages.

    Example:
        service = QualityScoreService(posts, ai)
        next_step = await service.score_post("post-id")
    """

    def __init__(
        self,
        posts: PostRepository,
        ai: AiService,
        metadata_refresher: SourceMetadataRefresher | None = None,
        vpn_testing_enabled: bool = True,
    ) -> None:
        """
        Args:
            posts: Post repository.
            ai: AI service used for quality scoring.
            metadata_refresher: Optional Telegram adapter used to refresh
                source views/forwards/reactions after the 15-minute delay.
            vpn_testing_enabled: Whether VPN config posts should continue to
                the Iran worker after scoring.
        """
        self._posts = posts
        self._ai = ai
        self._metadata_refresher = metadata_refresher
        self._vpn_testing_enabled = vpn_testing_enabled

    async def score_post(self, post_id: str) -> QueueItemType:
        """
        Score a post and return the next queue step.

        Args:
            post_id: Internal post id.

        Returns:
            ``VPN_TEST`` for VPN config posts when testing is enabled,
            otherwise ``APPROVAL_REQUEST``.

        Raises:
            ApprovalStateError: When the post no longer exists.
        """
        post = await self._posts.get(post_id)
        if post is None:
            raise ApprovalStateError(f"Post {post_id} not found for scoring")
        await self._refresh_metrics(post)
        metrics = self._metrics_for_ai(post)
        scored_at = datetime.now(timezone.utc)
        try:
            result = await self._ai.score_post(post.text, post.category, metrics)
            post.quality_score = PostQualityScore(
                score=result.score,
                reason=result.reason,
                provider=result.provider,
                scored_at=scored_at,
                metrics=result.raw_metrics,
            )
            logger.info(
                "Quality scored post=%s score=%.1f/100 provider=%s",
                post_id,
                result.score,
                result.provider,
            )
        except QualityScoringError as exc:
            post.quality_score = PostQualityScore(
                score=50.0,
                reason="امتیازدهی هوش مصنوعی در دسترس نبود",
                provider="unavailable",
                scored_at=scored_at,
                metrics=metrics,
            )
            logger.error(
                "Quality scoring unavailable; using fallback post=%s error=%s",
                post_id,
                exc,
            )
        await self._posts.save(post)
        if post.vpn_configs and self._vpn_testing_enabled:
            return QueueItemType.VPN_TEST
        return QueueItemType.APPROVAL_REQUEST

    async def _refresh_metrics(self, post: Post) -> None:
        """
        Refresh source engagement metrics before AI scoring.

        Side effects:
            Mutates ``post.source_metrics`` when Telegram returns a newer
            snapshot. Errors are logged as warnings and do not block approval.
        """
        if self._metadata_refresher is None:
            return
        try:
            refreshed = await self._metadata_refresher.refresh_metrics(
                post.source_chat_id,
                post.source_message_id,
            )
        except Exception as exc:
            logger.warning(
                "Source metadata refresh failed post=%s chat=%s msg=%s error=%s",
                post.post_id,
                post.source_chat_id,
                post.source_message_id,
                exc,
            )
            return
        if refreshed is None:
            logger.warning(
                "Source metadata refresh returned no message post=%s chat=%s msg=%s",
                post.post_id,
                post.source_chat_id,
                post.source_message_id,
            )
            return
        post.source_metrics = refreshed
        logger.info(
            "Source metadata refreshed post=%s views=%s forwards=%s reactions=%s replies=%s",
            post.post_id,
            refreshed.views,
            refreshed.forwards,
            refreshed.reactions_count,
            refreshed.replies_count,
        )

    @staticmethod
    def _metrics_for_ai(post: Post) -> dict[str, object]:
        """Build a JSON-serializable metrics payload for the AI prompt."""
        metrics = post.source_metrics
        now = datetime.now(timezone.utc)
        source_published_at = QualityScoreService._as_utc(
            metrics.source_published_at
        )
        age_minutes: float | None = None
        if source_published_at is not None:
            age_minutes = max(0.0, (now - source_published_at).total_seconds() / 60)
        return {
            "views": metrics.views,
            "forwards": metrics.forwards,
            "replies_count": metrics.replies_count,
            "reactions_count": metrics.reactions_count,
            "source_published_at": source_published_at.isoformat()
            if source_published_at
            else None,
            "age_minutes": round(age_minutes, 1) if age_minutes is not None else None,
            "media_count": len(post.media),
            "has_media": bool(post.media),
            "text_length": len(post.text or ""),
            "category": post.category.value if post.category else None,
        }

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        """
        Return a timezone-aware UTC datetime.

        MongoDB drivers may return naive datetimes even for values originally
        stored as UTC. Treat naive values as UTC so scoring can safely compare
        them with ``datetime.now(timezone.utc)``.
        """
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
