# mypy: disable-error-code="call-arg"
"""Application tests for semantic enqueue, consistency and winner selection."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
from telegram_assist_bot.application.ai.schemas import SemanticDuplicateOutput
from telegram_assist_bot.application.ai.task_handlers.semantic_duplicate import (
    SemanticDuplicateHandler,
    SemanticDuplicateHandlerOutcome,
    SemanticDuplicateTaskValidationError,
    validate_semantic_consistency,
)
from telegram_assist_bot.application.detect_semantic_duplicate import (
    DetectSemanticDuplicate,
    SemanticDuplicateEnqueueOutcome,
)
from telegram_assist_bot.application.ports import (
    EnqueueJobOutcome,
    EnqueueJobResult,
    PostConcurrencyConflictError,
    SemanticDuplicateCandidate,
    SemanticDuplicatePostUpdateRequest,
)
from telegram_assist_bot.domain.advertisement import AdvertisementCheckResult
from telegram_assist_bot.domain.ai_job import AIJob
from telegram_assist_bot.domain.duplicates import (
    DuplicateCheckResult,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)
from telegram_assist_bot.shared.errors import ConfigurationError

_NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


class _Clock:
    def utc_now(self) -> datetime:
        return _NOW


class _Jobs:
    def __init__(self) -> None:
        self.job: AIJob | None = None

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        if self.job is None:
            self.job = job
            return EnqueueJobResult(EnqueueJobOutcome.CREATED, job)
        return EnqueueJobResult(EnqueueJobOutcome.ALREADY_EXISTS, self.job)

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.job if self.job and self.job.job_id == job_id else None


class _Posts:
    def __init__(self, post: Post) -> None:
        self.post = post

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        return self.post if post_id == self.post.post_id else None

    async def update_semantic_duplicate(
        self, request: SemanticDuplicatePostUpdateRequest
    ) -> Post:
        self.post = request.post
        return self.post


class _Candidates:
    def __init__(self, values: tuple[SemanticDuplicateCandidate, ...]) -> None:
        self.values = values
        self.calls = 0

    async def list_candidates(
        self, **kwargs: object
    ) -> tuple[SemanticDuplicateCandidate, ...]:
        self.calls += 1
        return self.values


def _post() -> Post:
    post = Post(
        PostId("current"),
        SourceMessageIdentity(-1001, 10),
        "source",
        "منبع",
        OriginalPostContent("متن فارسی با نیم‌فاصله 🚀", None),
        _NOW - timedelta(minutes=2),
        _NOW - timedelta(minutes=1),
    ).transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )
    ad = AdvertisementCheckResult(
        False, 0.5, "تبلیغ نیست", "p", "m", _NOW, "1.0.0", "1", 1, 0
    )
    return post.start_advertisement_check(
        job_id="ad", expected_processing_version=0, requested_at=_NOW
    ).apply_advertisement_result(ad, job_id="ad", expected_processing_version=1)


def _exact(duplicate: bool = False) -> DuplicateCheckResult:
    return DuplicateCheckResult(
        duplicate,
        PostId("exact") if duplicate else None,
        "ExactContentHash",
        1,
        1,
        "a" * 64,
        _NOW,
    )


def _candidate(name: str, minutes: int) -> SemanticDuplicateCandidate:
    return SemanticDuplicateCandidate(
        PostId(name),
        "متن نامزد بدون شناسهٔ داخلی",
        _NOW - timedelta(minutes=minutes),
        _NOW + timedelta(days=1),
    )


def _use_case(
    posts: _Posts, jobs: _Jobs, candidates: _Candidates
) -> DetectSemanticDuplicate:
    return DetectSemanticDuplicate(
        cast("Any", jobs),
        cast("Any", posts),
        cast("Any", candidates),
        PromptRegistry(),
        _Clock(),
    )


def test_disabled_and_exact_short_circuit_touch_no_candidates_or_jobs() -> None:
    async def scenario() -> None:
        posts, jobs, candidates = _Posts(_post()), _Jobs(), _Candidates(())
        use_case = _use_case(posts, jobs, candidates)
        disabled = await use_case.execute(
            posts.post,
            exact_result=_exact(),
            global_enabled=False,
            source_enabled=None,
            threshold=None,
            duplicate_policy=None,
            failure_policy=None,
        )
        exact = await use_case.execute(
            posts.post,
            exact_result=_exact(True),
            global_enabled=True,
            source_enabled=True,
            threshold=0.88,
            duplicate_policy=SemanticDuplicatePolicy.REJECT,
            failure_policy=SemanticDuplicateFailurePolicy.STOP_PROCESSING,
        )
        assert disabled.outcome is SemanticDuplicateEnqueueOutcome.DISABLED
        assert exact.outcome is SemanticDuplicateEnqueueOutcome.EXACT_DUPLICATE
        assert candidates.calls == 0
        assert jobs.job is None

    asyncio.run(scenario())


def test_enabled_requires_explicit_threshold_and_policies() -> None:
    async def scenario() -> None:
        use_case = _use_case(
            _Posts(_post()), _Jobs(), _Candidates((_candidate("a", 1),))
        )
        with pytest.raises(ConfigurationError):
            await use_case.execute(
                _post(),
                exact_result=_exact(),
                global_enabled=True,
                source_enabled=True,
                threshold=None,
                duplicate_policy=None,
                failure_policy=None,
            )

    asyncio.run(scenario())


def test_enqueue_is_unique_and_context_contains_no_internal_identity() -> None:
    async def scenario() -> None:
        posts, jobs = _Posts(_post()), _Jobs()
        use_case = _use_case(
            posts, jobs, _Candidates((_candidate("candidate-secret", 1),))
        )
        first = await use_case.execute(
            posts.post,
            exact_result=_exact(),
            global_enabled=True,
            source_enabled=True,
            threshold=0.88,
            duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
            failure_policy=SemanticDuplicateFailurePolicy.MANUAL_REVIEW,
        )
        second = await use_case.execute(
            posts.post,
            exact_result=_exact(),
            global_enabled=True,
            source_enabled=True,
            threshold=0.88,
            duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
            failure_policy=SemanticDuplicateFailurePolicy.MANUAL_REVIEW,
        )
        assert first.job is not None
        assert second.job is not None
        assert first.job.job_id == second.job.job_id
        assert first.job.schema_version == "2"
        assert first.job.prompt_version == "2.0.0"
        serialized = first.invocations[0].context.model_dump_json()
        assert "candidate-secret" not in serialized
        assert "نیم‌فاصله" in serialized

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("is_duplicate", "similarity", "valid"),
    [
        (True, 0.88, True),
        (False, 0.879999, True),
        (True, 0.879999, False),
        (False, 0.88, False),
    ],
)
def test_threshold_boundary_and_boolean_consistency(
    is_duplicate: bool, similarity: float, valid: bool
) -> None:
    output = SemanticDuplicateOutput(
        is_duplicate=is_duplicate, similarity=similarity, confidence=0.4, reason="دلیل"
    )
    if valid:
        validate_semantic_consistency(output, 0.88)
    else:
        with pytest.raises(SemanticDuplicateTaskValidationError):
            validate_semantic_consistency(output, 0.88)


def test_handler_selects_highest_similarity_and_applies_manual_review() -> None:
    async def scenario() -> None:
        posts, jobs = _Posts(_post()), _Jobs()
        candidates = _Candidates((_candidate("first", 1), _candidate("best", 2)))
        queued = await _use_case(posts, jobs, candidates).execute(
            posts.post,
            exact_result=_exact(),
            global_enabled=True,
            source_enabled=True,
            threshold=0.88,
            duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
            failure_policy=SemanticDuplicateFailurePolicy.MANUAL_REVIEW,
        )
        assert queued.job is not None
        claimed = queued.job.claim("worker", 60, _NOW)

        def result(similarity: float) -> dict[str, object]:
            ai = AIResult(
                success=True,
                task_type=AITaskType.SEMANTIC_DUPLICATE,
                provider_name="provider",
                model_name="model",
                result={
                    "is_duplicate": True,
                    "similarity": similarity,
                    "confidence": 0.6,
                    "reason": "مشابه است",
                },
                confidence=0.6,
                reason="مشابه است",
                prompt_version="2.0.0",
                schema_version="2",
                latency=None,
                input_tokens=None,
                output_tokens=None,
                attempt_number=1,
                fallback_count=0,
                created_at=_NOW,
            )
            return ai.model_dump(mode="json")

        completed = claimed.complete("worker", {}, _NOW)
        completed = replace(
            completed,
            semantic_candidate_results=[
                {"candidate_post_id": "first", "result": result(0.9)},
                {"candidate_post_id": "best", "result": result(0.95)},
            ],
        )
        jobs.job = completed
        handled = await SemanticDuplicateHandler(
            cast("Any", posts), cast("Any", jobs), cast("Any", candidates), _Clock()
        ).complete(
            job_id=completed.job_id,
            expected_job_version=completed.version,
            threshold=0.88,
            duplicate_policy=SemanticDuplicatePolicy.MANUAL_REVIEW,
        )
        assert handled.outcome is SemanticDuplicateHandlerOutcome.APPLIED
        assert posts.post.semantic_duplicate_result is not None
        assert posts.post.semantic_duplicate_result.matched_post_id == PostId("best")
        assert (
            posts.post.semantic_duplicate_manual_review_reason
            == "semantic_duplicate_detected"
        )

    asyncio.run(scenario())


def _semantic_ai_result(
    *,
    duplicate: bool = True,
    similarity: float = 0.9,
    candidate_reason: str = "similar",
) -> dict[str, object]:
    result = AIResult(
        success=True,
        task_type=AITaskType.SEMANTIC_DUPLICATE,
        provider_name="provider",
        model_name="model",
        result={
            "is_duplicate": duplicate,
            "similarity": similarity,
            "confidence": 0.6,
            "reason": candidate_reason,
        },
        confidence=0.6,
        reason=candidate_reason,
        prompt_version="2.0.0",
        schema_version="2",
        attempt_number=1,
        fallback_count=0,
        created_at=_NOW,
    )
    return result.model_dump(mode="json")


def _completed_semantic_job(
    candidate_results: list[object],
) -> AIJob:
    job = AIJob.create(
        "semantic-handler-job",
        "current",
        AITaskType.SEMANTIC_DUPLICATE.value,
        "2.0.0",
        "2",
        20,
        created_at=_NOW,
    ).claim("worker", 60, _NOW)
    return replace(
        job.complete("worker", {}, _NOW),
        semantic_candidate_results=cast("Any", candidate_results),
    )


@pytest.mark.parametrize(
    ("threshold", "policy"),
    [
        (1, SemanticDuplicatePolicy.REJECT),
        (-0.1, SemanticDuplicatePolicy.REJECT),
        (0.8, "reject"),
    ],
)
def test_handler_rejects_invalid_threshold_and_policy(
    threshold: object, policy: object
) -> None:
    async def scenario() -> None:
        with pytest.raises(SemanticDuplicateTaskValidationError):
            await SemanticDuplicateHandler(
                cast("Any", _Posts(_post())),
                cast("Any", _Jobs()),
                cast("Any", _Candidates(())),
                _Clock(),
            ).complete(
                job_id="missing",
                expected_job_version=0,
                threshold=cast("Any", threshold),
                duplicate_policy=cast("Any", policy),
            )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "candidate_results",
    [
        [],
        ["not-a-mapping"],
        [{"candidate_post_id": "unknown", "result": _semantic_ai_result()}],
        [{"candidate_post_id": "candidate", "result": {"invalid": True}}],
        [
            {
                "candidate_post_id": "candidate",
                "result": {
                    **_semantic_ai_result(),
                    "prompt_version": "old",
                },
            }
        ],
        [
            {
                "candidate_post_id": "candidate",
                "result": _semantic_ai_result(duplicate=False, similarity=0.9),
            }
        ],
    ],
)
def test_handler_rejects_malformed_or_inconsistent_candidate_results(
    candidate_results: list[object],
) -> None:
    async def scenario() -> None:
        jobs = _Jobs()
        jobs.job = _completed_semantic_job(candidate_results)
        with pytest.raises(SemanticDuplicateTaskValidationError):
            await SemanticDuplicateHandler(
                cast("Any", _Posts(_post())),
                cast("Any", jobs),
                cast("Any", _Candidates((_candidate("candidate", 1),))),
                _Clock(),
            ).complete(
                job_id=jobs.job.job_id,
                expected_job_version=jobs.job.version,
                threshold=0.8,
                duplicate_policy=SemanticDuplicatePolicy.REJECT,
            )

    asyncio.run(scenario())


def test_handler_reports_stale_and_concurrent_conflict_without_overwrite() -> None:
    class MissingPosts(_Posts):
        async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
            del post_id, as_of
            return None

    class ConflictingPosts(_Posts):
        async def update_semantic_duplicate(
            self, request: SemanticDuplicatePostUpdateRequest
        ) -> Post:
            del request
            raise PostConcurrencyConflictError

    async def scenario() -> None:
        jobs = _Jobs()
        jobs.job = _completed_semantic_job(
            [
                {
                    "candidate_post_id": "candidate",
                    "result": _semantic_ai_result(),
                }
            ]
        )
        candidates = _Candidates((_candidate("candidate", 1),))
        stale = await SemanticDuplicateHandler(
            cast("Any", MissingPosts(_post())),
            cast("Any", jobs),
            cast("Any", candidates),
            _Clock(),
        ).complete(
            job_id=jobs.job.job_id,
            expected_job_version=jobs.job.version,
            threshold=0.8,
            duplicate_policy=SemanticDuplicatePolicy.REJECT,
        )
        assert stale.outcome is SemanticDuplicateHandlerOutcome.STALE

        pending = _post().start_semantic_duplicate_check(
            job_id=jobs.job.job_id,
            expected_processing_version=0,
            requested_at=_NOW,
        )
        conflict = await SemanticDuplicateHandler(
            cast("Any", ConflictingPosts(pending)),
            cast("Any", jobs),
            cast("Any", candidates),
            _Clock(),
        ).complete(
            job_id=jobs.job.job_id,
            expected_job_version=jobs.job.version,
            threshold=0.8,
            duplicate_policy=SemanticDuplicatePolicy.REJECT,
        )
        assert conflict.outcome is SemanticDuplicateHandlerOutcome.CONFLICT

    asyncio.run(scenario())


def test_handler_applies_retry_failure_and_rejects_wrong_job_status() -> None:
    async def scenario() -> None:
        job = AIJob.create(
            "semantic-handler-job",
            "current",
            AITaskType.SEMANTIC_DUPLICATE.value,
            "2.0.0",
            "2",
            20,
            created_at=_NOW,
        ).claim("worker", 60, _NOW)
        waiting = replace(
            job.fail("worker", "safe", 30, _NOW),
            safe_last_failure_code="timeout",
        )
        jobs = _Jobs()
        jobs.job = waiting
        posts = _Posts(
            _post().start_semantic_duplicate_check(
                job_id=waiting.job_id,
                expected_processing_version=0,
                requested_at=_NOW,
            )
        )
        result = await SemanticDuplicateHandler(
            cast("Any", posts),
            cast("Any", jobs),
            cast("Any", _Candidates(())),
            _Clock(),
        ).fail(
            job_id=waiting.job_id,
            expected_job_version=waiting.version,
            policy=SemanticDuplicateFailurePolicy.RETRY_LATER,
        )
        assert result.outcome is SemanticDuplicateHandlerOutcome.RETRY_SCHEDULED
        with pytest.raises(SemanticDuplicateTaskValidationError):
            await SemanticDuplicateHandler(
                cast("Any", posts),
                cast("Any", jobs),
                cast("Any", _Candidates(())),
                _Clock(),
            ).fail(
                job_id=waiting.job_id,
                expected_job_version=waiting.version,
                policy=SemanticDuplicateFailurePolicy.STOP_PROCESSING,
            )

    asyncio.run(scenario())
