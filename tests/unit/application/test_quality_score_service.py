"""Unit tests for optional post quality scoring."""

from __future__ import annotations

from datetime import datetime

from src.application.ai_service import AiService
from src.application.quality_score_service import QualityScoreService
from src.domain.entities import Post, PostSourceMetrics
from src.domain.enums import PostCategory, QualityScoreStatus, QueueItemType
from tests.unit.application.fakes import (
    FakeAiProvider,
    FakePostRepository,
)


class TestQualityScoreService:
    """Tests for :class:`QualityScoreService`."""

    async def test_scores_post_and_returns_approval_next_step(self) -> None:
        posts = FakePostRepository()
        post = Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=1,
            text="خبر خوب",
            content_hash="hash",
            category=PostCategory.GENERAL_NEWS,
        )
        await posts.save(post)
        service = QualityScoreService(
            posts,
            AiService(FakeAiProvider(name="groq", score=7.5)),
        )

        next_step = await service.score_post("p1")

        assert next_step == QueueItemType.APPROVAL_REQUEST
        assert posts.posts["p1"].quality_score is not None
        assert posts.posts["p1"].quality_score.score == 7.5

    async def test_naive_source_datetime_is_treated_as_utc(self) -> None:
        posts = FakePostRepository()
        await posts.save(
            Post(
                post_id="p1",
                source_chat_id=-100,
                source_message_id=1,
                text="خبر",
                content_hash="hash",
                source_metrics=PostSourceMetrics(
                    source_published_at=datetime(2026, 7, 2, 16, 0, 0)
                ),
            )
        )
        service = QualityScoreService(
            posts,
            AiService(FakeAiProvider(name="groq", score=6.5)),
        )

        next_step = await service.score_post("p1")

        assert next_step == QueueItemType.APPROVAL_REQUEST
        assert posts.posts["p1"].quality_score is not None

    async def test_unavailable_status_is_stored_when_ai_fails(self) -> None:
        posts = FakePostRepository()
        await posts.save(
            Post(
                post_id="p1",
                source_chat_id=-100,
                source_message_id=1,
                text="خبر",
                content_hash="hash",
            )
        )
        service = QualityScoreService(posts, AiService(FakeAiProvider(fail=True)))

        next_step = await service.score_post("p1")

        assert next_step == QueueItemType.APPROVAL_REQUEST
        assert posts.posts["p1"].quality_score is None
        assert posts.posts["p1"].quality_score_status == QualityScoreStatus.UNAVAILABLE

    async def test_existing_score_is_not_recomputed_during_preview_retry(self) -> None:
        posts = FakePostRepository()
        await posts.save(
            Post(
                post_id="p1",
                source_chat_id=-100,
                source_message_id=10,
                text="خبر",
                content_hash="hash",
                source_metrics=PostSourceMetrics(views=200),
            )
        )
        provider = FakeAiProvider(name="groq", score=82)
        service = QualityScoreService(
            posts,
            AiService(provider),
        )

        await service.score_post("p1")
        await service.score_post("p1")

        assert provider.score_calls == 1
        assert posts.posts["p1"].quality_score.metrics["views"] == 200
