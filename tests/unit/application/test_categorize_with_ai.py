# mypy: disable-error-code="arg-type"
"""Unit tests for CategorizeWithAI usecase."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ai.task_handlers.categorization import (
    CategorizationHandler,
    CategorizationHandlerOutcome,
    CategorizationTaskValidationError,
)
from telegram_assist_bot.application.categorize_post import KeywordCategoryRule
from telegram_assist_bot.application.ports import PostConcurrencyConflictError
from telegram_assist_bot.application.ports.ai_cache_repository import AICacheEntry
from telegram_assist_bot.application.ports.ai_job_repository import (
    EnqueueJobOutcome,
    EnqueueJobResult,
)
from telegram_assist_bot.application.prepare_post_pipeline import PreparationInput
from telegram_assist_bot.application.use_cases.categorize_with_ai import (
    CategorizeWithAI,
)

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.cache_key import AICacheIdentity
    from telegram_assist_bot.application.ports import ContentPreparationRepository
    from telegram_assist_bot.application.ports.ai_cache_repository import (
        AICacheRepository,
    )
    from telegram_assist_bot.application.ports.ai_job_repository import AIJobRepository
    from telegram_assist_bot.application.ports.post_repository import (
        CategorizationPostUpdateRequest,
    )
from telegram_assist_bot.domain.ai_job import AIJob
from telegram_assist_bot.domain.categories import (
    CategorizationCheckFailure,
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
    Category,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)

_NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


class FakeClock:
    def utc_now(self) -> datetime:
        return _NOW


class FakeAIJobRepository:
    def __init__(self) -> None:
        self.jobs: dict[str, AIJob] = {}
        self.enqueue_calls = 0

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        self.enqueue_calls += 1
        self.jobs[job.job_id] = job
        return EnqueueJobResult(EnqueueJobOutcome.CREATED, job)

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.jobs.get(job_id)


class FakePostRepository:
    def __init__(self, post: Post) -> None:
        self.post = post
        self.update_calls = 0

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        if self.post.post_id != post_id:
            return None
        return self.post

    async def update_categorization(
        self, request: CategorizationPostUpdateRequest
    ) -> Post:
        self.update_calls += 1
        self.post = request.post
        return self.post


class FakeContentPreparationRepository:
    def __init__(self) -> None:
        self.results: dict[str, CategorizationResult] = {}

    async def get_category_result(self, post_id: PostId) -> CategorizationResult | None:
        return self.results.get(post_id.value)

    async def save_category_result(
        self, post_id: PostId, result: CategorizationResult
    ) -> CategorizationResult:
        self.results[post_id.value] = result
        return result


class FakeCacheRepository:
    def __init__(self, result: AIResult) -> None:
        self.result = result
        self.read_calls = 0

    async def get(
        self,
        identity: AICacheIdentity,
        *,
        as_of: datetime,
    ) -> AICacheEntry:
        self.read_calls += 1
        return AICacheEntry(
            identity=identity,
            result=self.result,
            created_at=as_of - timedelta(seconds=2),
            expires_at=as_of + timedelta(minutes=5),
        )


def _stored_post() -> Post:
    post = Post(
        post_id=PostId("post-1"),
        source_identity=SourceMessageIdentity(-1001, 1),
        source_channel_username="source",
        source_channel_display_name="منبع",
        original_content=OriginalPostContent("پست جدید با متن تستی اخبار", None),
        source_published_at=_NOW - timedelta(minutes=1),
        received_at=_NOW,
    )
    return post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


def test_categorize_with_ai_manual_override() -> None:
    """Manual override check should win and prevent job queueing."""
    config = MagicMock()
    config.features.ai_categorization_enabled = True
    config.features.advertisement_detection_enabled = False
    config.features.duplicate_detection_enabled = False
    config.categorization.categories = [Category("news", "اخبار")]
    config.categorization.method_order = ("ai", "source_default")
    config.categorization.fallback_policy = "fallback_baseline"
    config.categorization.aliases = {}

    post = _stored_post()
    content_repo = FakeContentPreparationRepository()
    post_repo = FakePostRepository(post)
    job_repo = FakeAIJobRepository()
    enqueue_job = EnqueueAIJob(cast("AIJobRepository", job_repo), FakeClock())

    usecase = CategorizeWithAI(
        config=config,
        content_repo=cast("ContentPreparationRepository", content_repo),
        post_repo=post_repo,
        enqueue_job=enqueue_job,
        clock=FakeClock(),
    )

    manual = CategorizationResult(
        category_id="news",
        method=CategorizationMethod.MANUAL,
        policy_version=1,
        assigned_at=_NOW,
    )

    request = PreparationInput(
        post_id=post.post_id,
        text="متن",
        caption=None,
        entities=(),
        source_username="source",
        media_hashes=(),
        categories=(Category("news", "اخبار"),),
        category_rules=(),
        source_default_category_id="news",
        destinations=(),
        now=_NOW,
        manual_category=manual,
    )

    res = asyncio.run(usecase.execute(request, post))

    assert res == manual
    assert job_repo.enqueue_calls == 0


def test_categorize_with_ai_prerequisites_check() -> None:
    """If post is rejected/not stored, AI job should not be enqueued."""
    config = MagicMock()
    config.features.ai_categorization_enabled = True
    config.features.advertisement_detection_enabled = False
    config.features.duplicate_detection_enabled = False
    config.categorization.categories = [Category("news", "اخبار")]
    config.categorization.method_order = ("ai", "source_default")
    config.categorization.fallback_policy = "fallback_baseline"
    config.categorization.aliases = {}

    post = _stored_post()
    object.__setattr__(post, "status", PostStatus.EXPIRED)

    content_repo = FakeContentPreparationRepository()
    post_repo = FakePostRepository(post)
    job_repo = FakeAIJobRepository()
    enqueue_job = EnqueueAIJob(cast("AIJobRepository", job_repo), FakeClock())

    usecase = CategorizeWithAI(
        config=config,
        content_repo=cast("ContentPreparationRepository", content_repo),
        post_repo=post_repo,
        enqueue_job=enqueue_job,
        clock=FakeClock(),
    )

    request = PreparationInput(
        post_id=post.post_id,
        text="متن",
        caption=None,
        entities=(),
        source_username="source",
        media_hashes=(),
        categories=(Category("news", "اخبار"),),
        category_rules=(),
        source_default_category_id="news",
        destinations=(),
        now=_NOW,
    )

    res = asyncio.run(usecase.execute(request, post))

    assert res is None
    assert job_repo.enqueue_calls == 0


def test_categorize_with_ai_miss_enqueues_job() -> None:
    """If cache miss, enqueue job and update post state to pending."""
    config = MagicMock()
    config.features.ai_categorization_enabled = True
    config.features.advertisement_detection_enabled = False
    config.features.duplicate_detection_enabled = False
    config.categorization.categories = [Category("news", "اخبار")]
    config.categorization.method_order = ("ai", "source_default")
    config.categorization.fallback_policy = "fallback_baseline"
    config.categorization.aliases = {}

    post = _stored_post()
    content_repo = FakeContentPreparationRepository()
    post_repo = FakePostRepository(post)
    job_repo = FakeAIJobRepository()
    enqueue_job = EnqueueAIJob(cast("AIJobRepository", job_repo), FakeClock())

    usecase = CategorizeWithAI(
        config=config,
        content_repo=cast("ContentPreparationRepository", content_repo),
        post_repo=post_repo,
        enqueue_job=enqueue_job,
        clock=FakeClock(),
    )

    request = PreparationInput(
        post_id=post.post_id,
        text="متن تستی",
        caption=None,
        entities=(),
        source_username="source",
        media_hashes=(),
        categories=(Category("news", "اخبار"),),
        category_rules=(),
        source_default_category_id="news",
        destinations=(),
        now=_NOW,
    )

    res = asyncio.run(usecase.execute(request, post))

    assert res is None
    assert job_repo.enqueue_calls == 1
    assert post_repo.post.categorization_state is CategorizationState.PENDING


def test_keyword_before_ai_avoids_cache_and_job() -> None:
    """A successful earlier baseline method must short-circuit every AI side effect."""
    config = MagicMock()
    config.features.ai_categorization_enabled = True
    config.features.advertisement_detection_enabled = False
    config.features.duplicate_detection_enabled = False
    config.categorization.method_order = ("keyword", "ai", "source_default")
    config.categorization.fallback_policy = "fallback_baseline"
    config.categorization.aliases = {}
    post = _stored_post()
    job_repo = FakeAIJobRepository()
    cache = FakeCacheRepository(_cached_ai_result("news"))
    usecase = CategorizeWithAI(
        config=config,
        content_repo=cast(
            "ContentPreparationRepository", FakeContentPreparationRepository()
        ),
        post_repo=FakePostRepository(post),
        enqueue_job=EnqueueAIJob(cast("AIJobRepository", job_repo), FakeClock()),
        clock=FakeClock(),
        cache_repo=cast("AICacheRepository", cache),
    )
    request = PreparationInput(
        post_id=post.post_id,
        text="خبر فوری فناوری",
        caption=None,
        entities=(),
        source_username="source",
        media_hashes=(),
        categories=(Category("news", "اخبار"),),
        category_rules=(KeywordCategoryRule("rule-1", "news", "فناوری", 10),),
        source_default_category_id="news",
        destinations=(),
        now=_NOW,
    )

    result = asyncio.run(usecase.execute(request, post))

    assert result is not None
    assert result.method is CategorizationMethod.KEYWORD
    assert cache.read_calls == 0
    assert job_repo.enqueue_calls == 0


def test_valid_cache_hit_assigns_ai_without_enqueue() -> None:
    """A compatible cache result keeps its real producer metadata."""
    config = MagicMock()
    config.features.ai_categorization_enabled = True
    config.features.advertisement_detection_enabled = False
    config.features.duplicate_detection_enabled = False
    config.categorization.method_order = ("ai", "source_default")
    config.categorization.fallback_policy = "fallback_baseline"
    config.categorization.aliases = {"خبر": "news"}
    post = _stored_post()
    content_repo = FakeContentPreparationRepository()
    post_repo = FakePostRepository(post)
    job_repo = FakeAIJobRepository()
    cache = FakeCacheRepository(_cached_ai_result("خبر"))
    usecase = CategorizeWithAI(
        config=config,
        content_repo=cast("ContentPreparationRepository", content_repo),
        post_repo=post_repo,
        enqueue_job=EnqueueAIJob(cast("AIJobRepository", job_repo), FakeClock()),
        clock=FakeClock(),
        cache_repo=cast("AICacheRepository", cache),
    )
    request = PreparationInput(
        post_id=post.post_id,
        text="متن فارسی با نیم‌فاصله و 😀",
        caption=None,
        entities=(),
        source_username="source",
        media_hashes=(),
        categories=(Category("news", "اخبار"),),
        category_rules=(),
        source_default_category_id="news",
        destinations=(),
        now=_NOW,
    )

    result = asyncio.run(usecase.execute(request, post))

    assert result is not None
    assert result.category_id == "news"
    assert result.method is CategorizationMethod.AI
    assert result.cache_hit is True
    assert result.provider_name == "provider-a"
    assert job_repo.enqueue_calls == 0
    assert post_repo.post.categorization_state is CategorizationState.AI_ASSIGNED


def _cached_ai_result(category_id: str) -> AIResult:
    return AIResult(
        success=True,
        task_type=AITaskType.CATEGORIZATION,
        provider_name="provider-a",
        model_name="model-a",
        result={
            "category_id": category_id,
            "confidence": 0.8,
            "reason": "دسته معتبر",
        },
        confidence=0.8,
        reason="دسته معتبر",
        prompt_version="2.0.0",
        schema_version="2",
        latency=None,
        input_tokens=None,
        output_tokens=None,
        attempt_number=1,
        fallback_count=0,
        cache_hit=True,
        cache_age_seconds=2.0,
        created_at=_NOW,
    )


def _handler_config() -> MagicMock:
    config = MagicMock()
    config.categorization.categories = [
        Category("news", "News"),
        Category("disabled", "Disabled", active=False),
    ]
    config.categorization.aliases = {"headline": "news", "old": "disabled"}
    config.categorization.method_order = ("ai", "source_default")
    config.categorization.keyword_rules = ()
    source = MagicMock()
    source.telegram_channel_id = -1001
    source.default_category_id = "news"
    config.source_channels = [source]
    return config


def _completed_categorization_job(
    category_id: str = "news",
    *,
    result_override: dict[str, object] | None = None,
) -> AIJob:
    job = AIJob.create(
        "category-handler-job",
        "post-1",
        AITaskType.CATEGORIZATION.value,
        "2.0.0",
        "2",
        20,
        created_at=_NOW,
    ).claim("worker", 60, _NOW)
    result = _cached_ai_result(category_id)
    if result_override is not None:
        result = result.model_copy(update={"result": result_override})
    completed = job.complete("worker", result.result or {}, _NOW)
    return replace(completed, normalized_result=result.model_dump(mode="json"))


def test_categorization_handler_applies_alias_and_is_idempotent_for_manual() -> None:
    """Normalized aliases apply, while an existing manual result always wins."""

    async def scenario() -> None:
        job = _completed_categorization_job("headline")
        jobs = FakeAIJobRepository()
        jobs.jobs[job.job_id] = job
        posts = FakePostRepository(_stored_post().enqueue_categorization(job.job_id))
        preparations = FakeContentPreparationRepository()
        handler = CategorizationHandler(
            posts=posts,
            ai_jobs=jobs,
            content_preparations=preparations,
            clock=FakeClock(),
            config=_handler_config(),
        )

        applied = await handler.complete(
            job_id=job.job_id,
            expected_job_version=job.version,
        )
        assert applied.outcome is CategorizationHandlerOutcome.APPLIED
        assert posts.post.categorization_result is not None
        assert posts.post.categorization_result.category_id == "news"

        manual_post = _stored_post().enqueue_categorization(job.job_id)
        manual = CategorizationResult(
            category_id="news",
            method=CategorizationMethod.MANUAL,
            policy_version=1,
            assigned_at=_NOW,
        )
        preparations.results[manual_post.post_id.value] = manual
        posts.post = manual_post
        preserved = await handler.complete(
            job_id=job.job_id,
            expected_job_version=job.version,
        )
        assert preserved.outcome is CategorizationHandlerOutcome.IDEMPOTENT
        assert posts.post.categorization_state is CategorizationState.PENDING

    asyncio.run(scenario())


def test_categorization_handler_uses_source_default_for_invalid_category() -> None:
    """Unknown and inactive alias targets use the configured baseline fallback."""

    async def scenario() -> None:
        for category_id in ("unknown", "old"):
            job = _completed_categorization_job(category_id)
            jobs = FakeAIJobRepository()
            jobs.jobs[job.job_id] = job
            posts = FakePostRepository(
                _stored_post().enqueue_categorization(job.job_id)
            )
            preparations = FakeContentPreparationRepository()
            result = await CategorizationHandler(
                posts=posts,
                ai_jobs=jobs,
                content_preparations=preparations,
                clock=FakeClock(),
                config=_handler_config(),
            ).complete(
                job_id=job.job_id,
                expected_job_version=job.version,
            )
            assert result.outcome is CategorizationHandlerOutcome.APPLIED
            assert posts.post.categorization_result is not None
            assert (
                posts.post.categorization_result.method
                is CategorizationMethod.SOURCE_DEFAULT
            )

    asyncio.run(scenario())


def test_categorization_handler_retry_failure_and_stale_post() -> None:
    """Durable retry metadata is applied once and missing Posts resolve stale."""

    async def scenario() -> None:
        base = AIJob.create(
            "category-handler-job",
            "post-1",
            AITaskType.CATEGORIZATION.value,
            "2.0.0",
            "2",
            20,
            created_at=_NOW,
        ).claim("worker", 60, _NOW)
        waiting = replace(
            base.fail("worker", "safe", 30, _NOW),
            safe_last_failure_code="rate_limit",
            attempted_candidates_count=2,
            retry_count=1,
            fallback_count=1,
        )
        jobs = FakeAIJobRepository()
        jobs.jobs[waiting.job_id] = waiting
        posts = FakePostRepository(
            _stored_post().enqueue_categorization(waiting.job_id)
        )
        handler = CategorizationHandler(
            posts=posts,
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=_handler_config(),
        )

        result = await handler.fail(
            job_id=waiting.job_id,
            expected_job_version=waiting.version,
            policy="retry_later",
        )
        assert result.outcome is CategorizationHandlerOutcome.RETRY_SCHEDULED
        assert posts.post.categorization_failure == CategorizationCheckFailure(
            policy="retry_later",
            failure_category="rate_limit",
            failed_at=_NOW,
            attempted_candidates_count=2,
            retry_count=1,
            fallback_count=1,
            next_retry_at=waiting.next_run_at,
        )

        class MissingPosts(FakePostRepository):
            async def get_by_id(
                self, post_id: PostId, *, as_of: datetime
            ) -> Post | None:
                del post_id, as_of
                return None

        missing = MissingPosts(_stored_post())
        stale = await CategorizationHandler(
            posts=missing,
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=_handler_config(),
        ).fail(
            job_id=waiting.job_id,
            expected_job_version=waiting.version,
            policy="retry_later",
        )
        assert stale.outcome is CategorizationHandlerOutcome.STALE
        assert stale.post is None

    asyncio.run(scenario())


def test_categorization_handler_rejects_invalid_job_and_result_contracts() -> None:
    """Persisted task identity, status and normalized schema are fail-closed."""

    async def scenario() -> None:
        jobs = FakeAIJobRepository()
        posts = FakePostRepository(_stored_post())
        handler = CategorizationHandler(
            posts=posts,
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=_handler_config(),
        )
        with pytest.raises(CategorizationTaskValidationError):
            await handler.complete(job_id="", expected_job_version=0)

        job = _completed_categorization_job(
            result_override={"category_id": "news", "confidence": "bad"}
        )
        jobs.jobs[job.job_id] = job
        with pytest.raises(CategorizationTaskValidationError):
            await handler.complete(
                job_id=job.job_id,
                expected_job_version=job.version,
            )

    asyncio.run(scenario())


def test_categorization_handler_final_failure_uses_baseline_and_manual_wins() -> None:
    """Use a deterministic fallback unless a manual result already exists."""

    async def scenario() -> None:
        base = AIJob.create(
            "category-handler-job",
            "post-1",
            AITaskType.CATEGORIZATION.value,
            "2.0.0",
            "2",
            20,
            max_attempts=1,
            created_at=_NOW,
        ).claim("worker", 60, _NOW)
        failed = replace(
            base.fail("worker", "safe", 30, _NOW),
            safe_last_failure_code="timeout",
        )
        jobs = FakeAIJobRepository()
        jobs.jobs[failed.job_id] = failed
        posts = FakePostRepository(_stored_post().enqueue_categorization(failed.job_id))
        preparations = FakeContentPreparationRepository()
        handler = CategorizationHandler(
            posts=posts,
            ai_jobs=jobs,
            content_preparations=preparations,
            clock=FakeClock(),
            config=_handler_config(),
        )
        applied = await handler.fail(
            job_id=failed.job_id,
            expected_job_version=failed.version,
            policy="fallback_baseline",
        )
        assert applied.outcome is CategorizationHandlerOutcome.APPLIED
        assert posts.post.categorization_result is not None
        assert (
            posts.post.categorization_result.method
            is CategorizationMethod.SOURCE_DEFAULT
        )

        posts.post = _stored_post().enqueue_categorization(failed.job_id)
        preparations.results["post-1"] = CategorizationResult(
            "news",
            CategorizationMethod.MANUAL,
            1,
            _NOW,
        )
        preserved = await handler.fail(
            job_id=failed.job_id,
            expected_job_version=failed.version,
            policy="fallback_baseline",
        )
        assert preserved.outcome is CategorizationHandlerOutcome.IDEMPOTENT

    asyncio.run(scenario())


def test_categorization_handler_reports_terminal_post_and_cas_conflict() -> None:
    """Late completion is stale and a competing write is never overwritten."""

    class ConflictingPosts(FakePostRepository):
        async def update_categorization(
            self, request: CategorizationPostUpdateRequest
        ) -> Post:
            del request
            raise PostConcurrencyConflictError

    async def scenario() -> None:
        job = _completed_categorization_job()
        jobs = FakeAIJobRepository()
        jobs.jobs[job.job_id] = job
        expired = _stored_post().transition_to(
            PostStatus.EXPIRED,
            expected_version=1,
            occurred_at=_stored_post().expires_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="expired",
        )
        stale = await CategorizationHandler(
            posts=FakePostRepository(expired),
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=_handler_config(),
        ).complete(
            job_id=job.job_id,
            expected_job_version=job.version,
        )
        assert stale.outcome is CategorizationHandlerOutcome.STALE

        pending = _stored_post().enqueue_categorization(job.job_id)
        conflict = await CategorizationHandler(
            posts=ConflictingPosts(pending),
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=_handler_config(),
        ).complete(
            job_id=job.job_id,
            expected_job_version=job.version,
        )
        assert conflict.outcome is CategorizationHandlerOutcome.CONFLICT

    asyncio.run(scenario())


def test_categorization_handler_requires_completed_status_and_baseline_config() -> None:
    """Invalid durable status and missing baseline ordering fail closed."""

    async def scenario() -> None:
        pending = AIJob.create(
            "category-handler-job",
            "post-1",
            AITaskType.CATEGORIZATION.value,
            "2.0.0",
            "2",
            20,
            created_at=_NOW,
        )
        jobs = FakeAIJobRepository()
        jobs.jobs[pending.job_id] = pending
        handler = CategorizationHandler(
            posts=FakePostRepository(_stored_post()),
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=_handler_config(),
        )
        with pytest.raises(CategorizationTaskValidationError):
            await handler.complete(
                job_id=pending.job_id,
                expected_job_version=pending.version,
            )

        completed = _completed_categorization_job("unknown")
        jobs.jobs[completed.job_id] = completed
        config = _handler_config()
        config.categorization.method_order = ("ai",)
        handler = CategorizationHandler(
            posts=FakePostRepository(
                _stored_post().enqueue_categorization(completed.job_id)
            ),
            ai_jobs=jobs,
            content_preparations=FakeContentPreparationRepository(),
            clock=FakeClock(),
            config=config,
        )
        with pytest.raises(CategorizationTaskValidationError):
            await handler.complete(
                job_id=completed.job_id,
                expected_job_version=completed.version,
            )

    asyncio.run(scenario())
