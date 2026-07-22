"""Unit tests for isolated advertisement enqueue and completion handling."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
from telegram_assist_bot.application.ai.task_handlers.advertisement_detection import (
    AdvertisementDetectionHandler,
    AdvertisementHandlerOutcome,
    AdvertisementTaskValidationError,
)
from telegram_assist_bot.application.detect_advertisement import (
    AdvertisementEnqueueOutcome,
    DetectAdvertisement,
)
from telegram_assist_bot.application.ports import (
    AdvertisementPostUpdateRequest,
    EnqueueJobOutcome,
    EnqueueJobResult,
    PostConcurrencyConflictError,
)
from telegram_assist_bot.domain.advertisement import (
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)
from telegram_assist_bot.shared.errors import ConfigurationError

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AdvertisementPostRepository,
        AIJobRepository,
    )
    from telegram_assist_bot.application.ports.ai_audit_repository import (
        AIAuditEvent,
        AIAuditRepository,
    )

_NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)
_TEXT = "خبر فارسی با نیم‌فاصله، خط دوم و Emoji 🚀\nبدون تغییر"


class _Clock:
    def __init__(self, now: datetime = _NOW) -> None:
        self.now = now

    def utc_now(self) -> datetime:
        return self.now


class _JobRepository:
    def __init__(self) -> None:
        self.jobs_by_id: dict[str, AIJob] = {}
        self.keys: dict[str, str] = {}
        self.enqueue_calls = 0
        self._lock = asyncio.Lock()

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        async with self._lock:
            self.enqueue_calls += 1
            existing_id = self.keys.get(job.idempotency_key)
            if existing_id is not None:
                return EnqueueJobResult(
                    EnqueueJobOutcome.ALREADY_EXISTS,
                    self.jobs_by_id[existing_id],
                )
            self.jobs_by_id[job.job_id] = job
            self.keys[job.idempotency_key] = job.job_id
            return EnqueueJobResult(EnqueueJobOutcome.CREATED, job)

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.jobs_by_id.get(job_id)


class _PostRepository:
    def __init__(self, post: Post) -> None:
        self.post = post
        self.update_calls = 0
        self._lock = asyncio.Lock()

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        if self.post.post_id != post_id or self.post.is_expired_at(as_of):
            return None
        return self.post

    async def update_advertisement(
        self,
        request: AdvertisementPostUpdateRequest,
    ) -> Post:
        async with self._lock:
            self.update_calls += 1
            if (
                self.post.advertisement_processing_version
                != request.expected_processing_version
                or self.post.advertisement_state
                is not request.expected_processing_state
            ):
                raise PostConcurrencyConflictError
            self.post = request.post
            return self.post


class _ExplodingRepository:
    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"disabled feature accessed repository: {name}")


class _AuditRepository:
    def __init__(self) -> None:
        self.events: list[AIAuditEvent] = []

    async def append(self, event: AIAuditEvent) -> bool:
        self.events.append(event)
        return True


def _stored_post(post_id: str = "post-ad-unit") -> Post:
    post = Post(
        post_id=PostId(post_id),
        source_identity=SourceMessageIdentity(-1001, 77),
        source_channel_username="source",
        source_channel_display_name="منبع فارسی",
        original_content=OriginalPostContent(_TEXT, None),
        source_published_at=_NOW - timedelta(minutes=1),
        received_at=_NOW - timedelta(seconds=1),
    )
    return post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


async def _enqueue(
    post_repo: _PostRepository,
    job_repo: _JobRepository,
) -> tuple[Post, AIJob]:
    use_case = DetectAdvertisement(
        cast("AIJobRepository", job_repo),
        cast("AdvertisementPostRepository", post_repo),
        PromptRegistry(),
        _Clock(),
    )
    result = await use_case.execute(
        post_repo.post,
        global_enabled=True,
        source_enabled=True,
        failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
    )
    assert result.job is not None
    return post_repo.post, result.job


def _completed_job(
    job: AIJob,
    *,
    is_advertisement: bool,
    confidence: float = 0.8,
    cache_hit: bool = False,
) -> AIJob:
    claimed = job.claim("worker", 60, _NOW)
    ai_result = AIResult(
        success=True,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name="provider-a",
        model_name="model-a",
        result={
            "is_advertisement": is_advertisement,
            "confidence": confidence,
            "reason": "دلیل معتبر فارسی ✨",
        },
        confidence=confidence,
        reason="دلیل معتبر فارسی ✨",
        prompt_version=job.prompt_version,
        schema_version=job.schema_version,
        latency=None,
        input_tokens=None,
        output_tokens=None,
        attempt_number=1,
        fallback_count=0,
        cache_hit=cache_hit,
        cache_age_seconds=2.0 if cache_hit else None,
        created_at=_NOW + timedelta(seconds=1),
    )
    completed = claimed.complete(
        "worker",
        ai_result.result or {},
        _NOW + timedelta(seconds=2),
    )
    return replace(completed, normalized_result=ai_result.model_dump(mode="json"))


@pytest.mark.parametrize(
    ("global_enabled", "source_enabled"),
    [(False, None), (False, True), (True, False)],
)
def test_disabled_flags_create_no_job_or_repository_activity(
    global_enabled: bool,
    source_enabled: bool | None,
) -> None:
    async def scenario() -> None:
        exploding = cast("AIJobRepository", _ExplodingRepository())
        posts = cast("AdvertisementPostRepository", _ExplodingRepository())
        use_case = DetectAdvertisement(exploding, posts, PromptRegistry(), _Clock())

        result = await use_case.execute(
            _stored_post(),
            global_enabled=global_enabled,
            source_enabled=source_enabled,
            failure_policy=None,
        )

        assert result.outcome is AdvertisementEnqueueOutcome.DISABLED
        assert result.job is None
        assert result.request_context is None

    asyncio.run(scenario())


def test_enabled_flags_require_explicit_failure_policy_before_enqueue() -> None:
    async def scenario() -> None:
        job_repo = _JobRepository()
        post_repo = _PostRepository(_stored_post())
        use_case = DetectAdvertisement(
            cast("AIJobRepository", job_repo),
            cast("AdvertisementPostRepository", post_repo),
            PromptRegistry(),
            _Clock(),
        )
        with pytest.raises(ConfigurationError):
            await use_case.execute(
                post_repo.post,
                global_enabled=True,
                source_enabled=True,
                failure_policy=None,
            )
        assert job_repo.enqueue_calls == 0
        assert post_repo.update_calls == 0

    asyncio.run(scenario())


def test_enqueue_is_unique_and_preserves_exact_persian_context() -> None:
    async def scenario() -> None:
        job_repo = _JobRepository()
        post_repo = _PostRepository(_stored_post())
        use_case = DetectAdvertisement(
            cast("AIJobRepository", job_repo),
            cast("AdvertisementPostRepository", post_repo),
            PromptRegistry(),
            _Clock(),
        )
        first, second = await asyncio.gather(
            use_case.execute(
                post_repo.post,
                global_enabled=True,
                source_enabled=True,
                failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
            ),
            use_case.execute(
                post_repo.post,
                global_enabled=True,
                source_enabled=True,
                failure_policy=AdvertisementFailurePolicy.MANUAL_REVIEW,
            ),
        )

        assert first.job is not None
        assert second.job is not None
        assert first.job.job_id == second.job.job_id
        assert first.job.idempotency_key == (
            "post-ad-unit:advertisement_detection:1.0.0:1"
        )
        assert first.request_context is not None
        assert first.request_context.text == _TEXT
        assert len(job_repo.jobs_by_id) == 1
        assert post_repo.post.advertisement_state is (
            AdvertisementProcessingState.PENDING
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("is_advertisement", "expected_state"),
    [
        (True, AdvertisementProcessingState.REJECTED_AS_ADVERTISEMENT),
        (False, AdvertisementProcessingState.PASSED),
    ],
)
def test_valid_result_and_cache_hit_share_one_contract(
    is_advertisement: bool,
    expected_state: AdvertisementProcessingState,
) -> None:
    async def scenario() -> None:
        jobs = _JobRepository()
        posts = _PostRepository(_stored_post())
        _, job = await _enqueue(posts, jobs)
        completed = _completed_job(
            job,
            is_advertisement=is_advertisement,
            confidence=0.01,
            cache_hit=True,
        )
        jobs.jobs_by_id[job.job_id] = completed
        audit = _AuditRepository()
        handler = AdvertisementDetectionHandler(
            cast("AdvertisementPostRepository", posts),
            cast("AIJobRepository", jobs),
            _Clock(_NOW + timedelta(seconds=3)),
            cast("AIAuditRepository", audit),
        )

        handled = await handler.complete(
            job_id=job.job_id,
            expected_job_version=completed.version,
        )
        duplicate = await handler.complete(
            job_id=job.job_id,
            expected_job_version=completed.version,
        )

        assert handled.outcome is AdvertisementHandlerOutcome.APPLIED
        assert duplicate.outcome is AdvertisementHandlerOutcome.IDEMPOTENT
        assert posts.post.advertisement_state is expected_state
        assert posts.post.advertisement_result is not None
        assert posts.post.advertisement_result.cache_hit
        assert posts.post.advertisement_result.confidence == 0.01
        assert len(audit.events) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("policy", "expected_state"),
    [
        (
            AdvertisementFailurePolicy.CONTINUE_PROCESSING,
            AdvertisementProcessingState.FAILED_CONTINUE,
        ),
        (
            AdvertisementFailurePolicy.STOP_PROCESSING,
            AdvertisementProcessingState.PROCESSING_STOPPED,
        ),
        (
            AdvertisementFailurePolicy.MANUAL_REVIEW,
            AdvertisementProcessingState.MANUAL_REVIEW_REQUIRED,
        ),
    ],
)
def test_terminal_failure_policies_never_fabricate_result(
    policy: AdvertisementFailurePolicy,
    expected_state: AdvertisementProcessingState,
) -> None:
    async def scenario() -> None:
        jobs = _JobRepository()
        posts = _PostRepository(_stored_post())
        _, job = await _enqueue(posts, jobs)
        job = replace(job, max_attempts=1)
        claimed = job.claim("worker", 60, _NOW)
        failed = replace(
            claimed.fail("worker", "safe", 30, _NOW + timedelta(seconds=1)),
            attempted_candidates_count=2,
            retry_count=1,
            fallback_count=1,
            safe_last_failure_code="timeout",
        )
        assert failed.status is AIJobStatus.ALL_PROVIDERS_FAILED
        jobs.jobs_by_id[job.job_id] = failed
        handler = AdvertisementDetectionHandler(
            cast("AdvertisementPostRepository", posts),
            cast("AIJobRepository", jobs),
            _Clock(_NOW + timedelta(seconds=2)),
        )

        handled = await handler.fail(
            job_id=job.job_id,
            expected_job_version=failed.version,
            policy=policy,
        )

        assert handled.outcome is AdvertisementHandlerOutcome.APPLIED
        assert posts.post.advertisement_state is expected_state
        assert posts.post.advertisement_result is None
        assert posts.post.advertisement_failure is not None

    asyncio.run(scenario())


def test_retry_later_reuses_existing_future_job_schedule() -> None:
    async def scenario() -> None:
        jobs = _JobRepository()
        posts = _PostRepository(_stored_post())
        _, job = await _enqueue(posts, jobs)
        job = replace(job, max_attempts=2)
        claimed = job.claim("worker", 60, _NOW)
        failed = replace(
            claimed.fail("worker", "safe", 30, _NOW + timedelta(seconds=1)),
            attempted_candidates_count=1,
            retry_count=1,
            fallback_count=0,
            safe_last_failure_code="timeout",
        )
        assert failed.status is AIJobStatus.WAITING_FOR_RETRY
        jobs.jobs_by_id[job.job_id] = failed
        handler = AdvertisementDetectionHandler(
            cast("AdvertisementPostRepository", posts),
            cast("AIJobRepository", jobs),
            _Clock(_NOW + timedelta(seconds=2)),
        )

        handled = await handler.fail(
            job_id=job.job_id,
            expected_job_version=failed.version,
            policy=AdvertisementFailurePolicy.RETRY_LATER,
        )

        assert handled.outcome is AdvertisementHandlerOutcome.RETRY_SCHEDULED
        assert posts.post.advertisement_state is (
            AdvertisementProcessingState.RETRY_PENDING
        )
        assert posts.post.advertisement_failure is not None
        assert posts.post.advertisement_failure.next_retry_at == failed.next_run_at
        assert len(jobs.jobs_by_id) == 1

    asyncio.run(scenario())


def test_old_prompt_or_stale_job_version_is_rejected_without_post_write() -> None:
    async def scenario() -> None:
        jobs = _JobRepository()
        posts = _PostRepository(_stored_post())
        _, job = await _enqueue(posts, jobs)
        completed = _completed_job(job, is_advertisement=False)
        normalized = dict(completed.normalized_result or {})
        normalized["prompt_version"] = "old-version"
        completed = replace(completed, normalized_result=normalized)
        jobs.jobs_by_id[job.job_id] = completed
        handler = AdvertisementDetectionHandler(
            cast("AdvertisementPostRepository", posts),
            cast("AIJobRepository", jobs),
            _Clock(_NOW + timedelta(seconds=3)),
        )

        with pytest.raises(AdvertisementTaskValidationError):
            await handler.complete(
                job_id=job.job_id,
                expected_job_version=completed.version,
            )
        with pytest.raises(AdvertisementTaskValidationError):
            await handler.complete(
                job_id=job.job_id,
                expected_job_version=completed.version - 1,
            )
        assert posts.post.advertisement_state is AdvertisementProcessingState.PENDING

    asyncio.run(scenario())


def test_competing_completion_handlers_commit_at_most_once() -> None:
    async def scenario() -> None:
        jobs = _JobRepository()
        posts = _PostRepository(_stored_post())
        _, job = await _enqueue(posts, jobs)
        completed = _completed_job(job, is_advertisement=False)
        jobs.jobs_by_id[job.job_id] = completed
        handler = AdvertisementDetectionHandler(
            cast("AdvertisementPostRepository", posts),
            cast("AIJobRepository", jobs),
            _Clock(_NOW + timedelta(seconds=3)),
        )

        outcomes = await asyncio.gather(
            handler.complete(
                job_id=job.job_id,
                expected_job_version=completed.version,
            ),
            handler.complete(
                job_id=job.job_id,
                expected_job_version=completed.version,
            ),
        )

        assert {item.outcome for item in outcomes} <= {
            AdvertisementHandlerOutcome.APPLIED,
            AdvertisementHandlerOutcome.IDEMPOTENT,
        }
        assert (
            sum(
                item.outcome is AdvertisementHandlerOutcome.APPLIED for item in outcomes
            )
            == 1
        )
        assert posts.post.advertisement_processing_version == 2

    asyncio.run(scenario())
