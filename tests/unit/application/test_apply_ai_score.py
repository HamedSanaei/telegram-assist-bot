"""Unit tests for validated score persistence and idempotent fan-out."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

import pytest

from telegram_assist_bot.application.ai.contracts import AIResult
from telegram_assist_bot.application.ai.task_handlers.scoring import ScoringHandler
from telegram_assist_bot.application.use_cases.apply_ai_score import (
    ApplyAIScore,
    ApplyScoreOutcome,
    ScoringTaskValidationError,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.domain.ai_task import AITaskType
from telegram_assist_bot.domain.scoring import ScoringState

from .test_delayed_ai_scoring import FakePosts, _eligible_post

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import AIJobRepository
    from telegram_assist_bot.domain.posts import Post, PostId

_NOW = datetime(2026, 7, 19, 8, 0, tzinfo=UTC)


class Clock:
    def utc_now(self) -> datetime:
        return _NOW


class Jobs:
    def __init__(self, job: AIJob) -> None:
        self.job = job

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.job if self.job.job_id == job_id else None

    async def update(self, job: AIJob) -> None:
        self.job = job


class Fanout:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, *, post_id: str, version: int, now: datetime) -> None:
        self.calls += 1


def _completed_job(payload: dict[str, object], *, prompt: str = "2.0.0") -> AIJob:
    result = AIResult(
        success=True,
        task_type=AITaskType.SCORING,
        provider_name="provider-a",
        model_name="model-a",
        result=payload,
        prompt_version=prompt,
        schema_version="2",
        attempt_number=1,
        fallback_count=0,
        created_at=_NOW,
        confidence=None,
        reason=None,
        latency=None,
        input_tokens=None,
        output_tokens=None,
    )
    job = AIJob.create(
        "job-score",
        "post-score-1",
        AITaskType.SCORING.value,
        prompt,
        "2",
        10,
        created_at=_NOW,
    )
    return replace(
        job,
        status=AIJobStatus.COMPLETED,
        normalized_result=result.model_dump(mode="json"),
        version=1,
    )


def _scheduled_post() -> Post:
    post = _eligible_post()
    return post.schedule_scoring(
        job_id="job-score",
        due_at=_NOW,
        expected_processing_version=0,
    )


def _config(policy: str = "retry_later") -> MagicMock:
    config = MagicMock()
    config.scoring.failure_policy.value = policy
    return config


def test_score_zero_and_optional_absence_are_persisted_once() -> None:
    job = _completed_job(
        {"score": 0, "confidence": 0.0, "reason": "امتیاز صفر معتبر است ✨"}
    )
    posts = FakePosts(_scheduled_post())
    fanout = Fanout()
    use_case = ApplyAIScore(
        posts, cast("AIJobRepository", Jobs(job)), Clock(), _config(), fanout
    )

    first = asyncio.run(use_case.complete(job_id=job.job_id, expected_job_version=1))
    second = asyncio.run(use_case.complete(job_id=job.job_id, expected_job_version=1))

    assert first.outcome is ApplyScoreOutcome.APPLIED
    assert second.outcome is ApplyScoreOutcome.IDEMPOTENT
    assert posts.post.scoring_state is ScoringState.COMPLETED
    assert posts.post.scoring_result is not None
    assert posts.post.scoring_result.score == 0
    assert posts.post.scoring_result.headline_quality is None
    assert fanout.calls == 1


def test_score_100_and_components_are_preserved() -> None:
    job = _completed_job(
        {
            "score": 100,
            "confidence": 1.0,
            "reason": "عالی",
            "attractiveness_probability": 0.9,
            "headline_quality": 100,
        }
    )
    posts = FakePosts(_scheduled_post())
    result = asyncio.run(
        ApplyAIScore(
            posts, cast("AIJobRepository", Jobs(job)), Clock(), _config()
        ).complete(job_id=job.job_id, expected_job_version=1)
    )
    assert result.post is not None
    assert result.post.scoring_result is not None
    assert result.post.scoring_result.score == 100
    assert result.post.scoring_result.attractiveness_probability == 0.9


def test_old_prompt_completion_is_rejected() -> None:
    job = _completed_job(
        {"score": 50, "confidence": 0.8, "reason": "قدیمی"}, prompt="1.0.0"
    )
    with pytest.raises(ScoringTaskValidationError):
        asyncio.run(
            ApplyAIScore(
                FakePosts(_scheduled_post()),
                cast("AIJobRepository", Jobs(job)),
                Clock(),
                _config(),
            ).complete(job_id=job.job_id, expected_job_version=1)
        )


def test_retry_and_exhaustion_never_fabricate_score() -> None:
    scheduled = _scheduled_post()
    waiting = replace(
        _completed_job({"score": 50, "confidence": 0.8, "reason": "x"}),
        status=AIJobStatus.WAITING_FOR_RETRY,
        normalized_result=None,
        next_run_at=_NOW.replace(minute=1),
        safe_last_failure_code="timeout",
    )
    posts = FakePosts(scheduled)
    retry = asyncio.run(
        ApplyAIScore(
            posts, cast("AIJobRepository", Jobs(waiting)), Clock(), _config()
        ).fail(job_id=waiting.job_id, expected_job_version=1)
    )
    assert retry.outcome is ApplyScoreOutcome.RETRY_SCHEDULED
    assert posts.post.scoring_result is None

    exhausted = replace(
        waiting,
        status=AIJobStatus.ALL_PROVIDERS_FAILED,
        next_run_at=_NOW,
        version=2,
    )
    unavailable = asyncio.run(
        ApplyAIScore(
            posts, cast("AIJobRepository", Jobs(exhausted)), Clock(), _config()
        ).fail(job_id=exhausted.job_id, expected_job_version=2)
    )
    assert unavailable.outcome is ApplyScoreOutcome.UNAVAILABLE
    assert posts.post.scoring_state is ScoringState.UNAVAILABLE
    assert posts.post.scoring_result is None


def test_expired_post_is_resolved_before_provider_input() -> None:
    """Expire the claimed Job and mark scoring stale without exposing Post text."""
    scheduled = _scheduled_post()

    class ExpiredPosts(FakePosts):
        async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
            del post_id, as_of
            return None

    processing = replace(
        _completed_job({"score": 50, "confidence": 0.8, "reason": "معتبر"}),
        status=AIJobStatus.PROCESSING,
        normalized_result=None,
        lease_owner="worker-a",
        lease_expires_at=_NOW.replace(minute=1),
    )
    posts = ExpiredPosts(scheduled)
    jobs = Jobs(processing)
    apply_score = ApplyAIScore(posts, cast("AIJobRepository", jobs), Clock(), _config())
    handler = ScoringHandler(
        posts,
        cast("AIJobRepository", jobs),
        Clock(),
        apply_score,
    )

    context = asyncio.run(
        handler.prepare_claimed(
            job_id=processing.job_id,
            expected_job_version=processing.version,
            lease_owner="worker-a",
        )
    )

    assert context is None
    assert posts.post.scoring_state is ScoringState.STALE_OR_EXPIRED
    assert jobs.job.status is AIJobStatus.EXPIRED
