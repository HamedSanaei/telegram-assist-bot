"""Unit tests for explicit delayed AI scoring scheduling."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ports.ai_job_repository import (
    EnqueueJobOutcome,
    EnqueueJobResult,
)
from telegram_assist_bot.application.use_cases.schedule_ai_scoring import (
    ApprovalScoringBoundary,
    ScheduleAIScoring,
    ScoringScheduleOutcome,
    ScoringScheduleResult,
)
from telegram_assist_bot.domain.advertisement import (
    AdvertisementCheckResult,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
)
from telegram_assist_bot.domain.duplicates import (
    SemanticDuplicateResult,
    SemanticDuplicateState,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)
from telegram_assist_bot.domain.scoring import ScoringState

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports.ai_job_repository import AIJobRepository
    from telegram_assist_bot.application.ports.post_repository import (
        ScoringPostUpdateRequest,
    )
    from telegram_assist_bot.domain.ai_job import AIJob

_NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)


class FakeClock:
    def utc_now(self) -> datetime:
        return _NOW


class FakeJobs:
    def __init__(self) -> None:
        self.by_key: dict[str, AIJob] = {}

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        existing = self.by_key.get(job.idempotency_key)
        if existing is not None:
            return EnqueueJobResult(EnqueueJobOutcome.ALREADY_EXISTS, existing)
        self.by_key[job.idempotency_key] = job
        return EnqueueJobResult(EnqueueJobOutcome.CREATED, job)


class FakePosts:
    def __init__(self, post: Post) -> None:
        self.post = post
        self.updates = 0

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        return self.post if post_id == self.post.post_id else None

    async def get_for_scoring_completion(self, post_id: PostId) -> Post | None:
        return self.post if post_id == self.post.post_id else None

    async def update_scoring(self, request: ScoringPostUpdateRequest) -> Post:
        self.updates += 1
        self.post = request.post
        return self.post


def _eligible_post() -> Post:
    advertisement = AdvertisementCheckResult(
        False,
        0.9,
        "تبلیغ نیست",
        "provider",
        "model",
        _NOW,
        "1.0.0",
        "1",
        1,
        0,
    )
    semantic = SemanticDuplicateResult(
        False,
        0.2,
        0.9,
        None,
        "تکراری نیست",
        "provider",
        "model",
        _NOW,
        "2.0.0",
        "2",
        1,
        0,
    )
    category = CategorizationResult(
        "news",
        CategorizationMethod.SOURCE_DEFAULT,
        1,
        _NOW,
        reason="source_default",
    )
    post = Post(
        PostId("post-score-1"),
        SourceMessageIdentity(-1001, 5),
        "source",
        "منبع",
        OriginalPostContent("متن فارسی با نیم‌فاصله‌ و Emoji ✨", None),
        _NOW - timedelta(minutes=30),
        _NOW - timedelta(minutes=29),
        advertisement_state=AdvertisementProcessingState.PASSED,
        advertisement_processing_version=1,
        advertisement_job_id="job-ad",
        advertisement_result=advertisement,
        semantic_duplicate_state=SemanticDuplicateState.PASSED,
        semantic_duplicate_version=1,
        semantic_duplicate_job_id="job-sem",
        semantic_duplicate_result=semantic,
        categorization_state=CategorizationState.SOURCE_DEFAULT_FALLBACK,
        categorization_processing_version=1,
        categorization_result=category,
    )
    return post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW - timedelta(minutes=29),
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


def _config(enabled: bool = True) -> MagicMock:
    config = MagicMock()
    config.features.ai_scoring_enabled = enabled
    config.scoring.delay_seconds = 1200
    config.scoring.failure_policy.value = "retry_later"
    config.ai.queue.max_attempts = 3
    return config


def test_disabled_scoring_has_no_job_or_pending_state() -> None:
    post = _eligible_post()
    jobs = FakeJobs()
    posts = FakePosts(post)
    use_case = ScheduleAIScoring(
        _config(False),
        posts,
        EnqueueAIJob(cast("AIJobRepository", jobs), FakeClock()),
    )

    result = asyncio.run(use_case.execute(post, boundary=ApprovalScoringBoundary.READY))

    assert result.outcome is ScoringScheduleOutcome.DISABLED
    assert jobs.by_key == {}
    assert posts.updates == 0
    assert post.scoring_state is ScoringState.NOT_REQUESTED


def test_scoring_requires_explicit_approval_application_boundary() -> None:
    """Do not enqueue merely because prerequisite content stages completed."""
    post = _eligible_post()
    jobs = FakeJobs()
    posts = FakePosts(post)

    result = asyncio.run(
        ScheduleAIScoring(
            _config(),
            posts,
            EnqueueAIJob(cast("AIJobRepository", jobs), FakeClock()),
        ).execute(post, boundary=None)
    )

    assert result.outcome is ScoringScheduleOutcome.INELIGIBLE
    assert jobs.by_key == {}
    assert posts.updates == 0


def test_due_at_uses_absolute_source_publication_time_and_is_idempotent() -> None:
    post = _eligible_post()
    jobs = FakeJobs()
    posts = FakePosts(post)
    use_case = ScheduleAIScoring(
        _config(),
        posts,
        EnqueueAIJob(cast("AIJobRepository", jobs), FakeClock()),
    )

    first = asyncio.run(use_case.execute(post, boundary=ApprovalScoringBoundary.READY))
    second = asyncio.run(
        use_case.execute(posts.post, boundary=ApprovalScoringBoundary.DELIVERED)
    )

    assert first.outcome is ScoringScheduleOutcome.SCHEDULED
    assert second.outcome is ScoringScheduleOutcome.ALREADY_SCHEDULED
    assert first.job is not None
    assert first.job.next_run_at == post.source_published_at + timedelta(seconds=1200)
    assert first.job.next_run_at.tzinfo is UTC
    assert len(jobs.by_key) == 1
    assert posts.updates == 1


def test_concurrent_enqueue_returns_one_logical_job() -> None:
    post = _eligible_post()
    jobs = FakeJobs()
    first_posts = FakePosts(post)
    second_posts = FakePosts(post)
    enqueue = EnqueueAIJob(cast("AIJobRepository", jobs), FakeClock())

    async def run() -> tuple[ScoringScheduleResult, ScoringScheduleResult]:
        return await asyncio.gather(
            ScheduleAIScoring(_config(), first_posts, enqueue).execute(
                post, boundary=ApprovalScoringBoundary.READY
            ),
            ScheduleAIScoring(_config(), second_posts, enqueue).execute(
                post, boundary=ApprovalScoringBoundary.DELIVERED
            ),
        )

    first, second = asyncio.run(run())
    assert first.job is not None
    assert second.job is not None
    assert first.job.job_id == second.job.job_id
    assert len(jobs.by_key) == 1
