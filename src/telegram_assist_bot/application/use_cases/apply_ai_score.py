"""Apply normalized delayed scores and scoring failures through Post CAS."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from pydantic import ValidationError

from telegram_assist_bot.application.ai.contracts import AIResult
from telegram_assist_bot.application.ai.schemas import ScoringOutput
from telegram_assist_bot.application.ports import (
    PostConcurrencyConflictError,
    ScoringPostUpdateRequest,
)
from telegram_assist_bot.domain.ai_job import AIJobStatus
from telegram_assist_bot.domain.ai_task import AITaskType
from telegram_assist_bot.domain.posts import PostId, PostStatus
from telegram_assist_bot.domain.scoring import (
    ScoringFailure,
    ScoringFailurePolicy,
    ScoringResult,
    ScoringState,
)

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports import (
        AIJobRepository,
        ScoringPostRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.ai_job import AIJob
    from telegram_assist_bot.domain.posts import Post
    from telegram_assist_bot.shared.config.models import ApplicationConfig


class ScoringFanout(Protocol):
    """Best-effort approval-header synchronization after committed scoring state."""

    async def execute(self, *, post_id: str, version: int, now: datetime) -> None:
        """Refresh current approval controls without mutating destination content."""
        ...


class ApplyScoreOutcome(StrEnum):
    """Describe one typed idempotent score application outcome."""

    APPLIED = "applied"
    RETRY_SCHEDULED = "retry_scheduled"
    UNAVAILABLE = "unavailable"
    IDEMPOTENT = "idempotent"
    STALE = "stale"
    CONFLICT = "conflict"


class ScoringTaskValidationError(Exception):
    """Reject stale or mismatched scoring task data without retaining payloads."""

    def __init__(self) -> None:
        """Create a payload-free stable validation error."""
        super().__init__("Persisted scoring task data is invalid.")


@dataclass(frozen=True, slots=True)
class ApplyScoreResult:
    """Return the canonical Post and whether first persistence occurred."""

    outcome: ApplyScoreOutcome
    post: Post | None


@dataclass(frozen=True, slots=True)
class ApplyAIScore:
    """Validate Job identity, persist once, then fan out independently."""

    posts: ScoringPostRepository = field(repr=False)
    jobs: AIJobRepository = field(repr=False)
    clock: Clock = field(repr=False)
    config: ApplicationConfig = field(repr=False)
    fanout: ScoringFanout | None = field(default=None, repr=False)

    async def complete(
        self, *, job_id: str, expected_job_version: int
    ) -> ApplyScoreResult:
        """Apply one completed normalized scoring result exactly once."""
        job = await self._load_job(job_id, expected_job_version)
        if job.status is not AIJobStatus.COMPLETED or job.normalized_result is None:
            raise ScoringTaskValidationError
        ai_result = self._normalized_result(job)
        try:
            output = ScoringOutput.model_validate(ai_result.result)
        except ValidationError:
            raise ScoringTaskValidationError from None
        post = await self.posts.get_by_id(
            PostId(job.post_id), as_of=self.clock.utc_now()
        )
        if post is None or post.status is PostStatus.EXPIRED:
            return ApplyScoreResult(ApplyScoreOutcome.STALE, post)
        if post.scoring_state is ScoringState.COMPLETED:
            return ApplyScoreResult(ApplyScoreOutcome.IDEMPOTENT, post)
        if post.scoring_state.is_terminal:
            return ApplyScoreResult(ApplyScoreOutcome.STALE, post)

        result = ScoringResult(
            score=output.score,
            confidence=output.confidence,
            reason=output.reason,
            provider_name=ai_result.provider_name,
            model_name=ai_result.model_name,
            scored_at=ai_result.created_at,
            prompt_version=ai_result.prompt_version,
            schema_version=ai_result.schema_version,
            attractiveness_probability=output.attractiveness_probability,
            engagement_probability=output.engagement_probability,
            headline_quality=output.headline_quality,
            freshness=output.freshness,
            news_value=output.news_value,
            writing_quality=output.writing_quality,
            cache_hit=ai_result.cache_hit,
            cache_age_seconds=ai_result.cache_age_seconds,
            attempt_number=ai_result.attempt_number,
            fallback_count=ai_result.fallback_count,
        )
        previous_state = post.scoring_state
        previous_version = post.scoring_processing_version
        target = post.apply_scoring_result(
            result,
            job_id=job.job_id,
            expected_processing_version=previous_version,
        )
        persisted, outcome = await self._persist(
            target, previous_state, previous_version
        )
        if outcome is ApplyScoreOutcome.APPLIED and self.fanout is not None:
            await self.fanout.execute(
                post_id=job.post_id,
                version=persisted.scoring_processing_version,
                now=self.clock.utc_now(),
            )
        return ApplyScoreResult(outcome, persisted)

    async def fail(self, *, job_id: str, expected_job_version: int) -> ApplyScoreResult:
        """Apply only the configured scoring retry or unavailable policy."""
        job = await self._load_job(job_id, expected_job_version)
        scoring = self.config.scoring
        if scoring is None:
            raise ScoringTaskValidationError
        post = await self.posts.get_by_id(
            PostId(job.post_id), as_of=self.clock.utc_now()
        )
        if post is None:
            return ApplyScoreResult(ApplyScoreOutcome.STALE, None)
        if post.scoring_state.is_terminal:
            return ApplyScoreResult(ApplyScoreOutcome.IDEMPOTENT, post)

        if job.status is AIJobStatus.WAITING_FOR_RETRY:
            if scoring.failure_policy.value != "retry_later":
                raise ScoringTaskValidationError
            policy = ScoringFailurePolicy.RETRY_LATER
            next_retry_at = job.next_run_at
            outcome = ApplyScoreOutcome.RETRY_SCHEDULED
        elif job.status is AIJobStatus.ALL_PROVIDERS_FAILED:
            policy = ScoringFailurePolicy.MARK_UNAVAILABLE
            next_retry_at = None
            outcome = ApplyScoreOutcome.UNAVAILABLE
        else:
            raise ScoringTaskValidationError
        failure = ScoringFailure(
            policy=policy,
            failure_category=job.safe_last_failure_code or "unknown",
            failed_at=job.updated_at or self.clock.utc_now(),
            next_retry_at=next_retry_at,
        )
        previous_state = post.scoring_state
        previous_version = post.scoring_processing_version
        target = post.apply_scoring_failure(
            failure,
            job_id=job.job_id,
            expected_processing_version=previous_version,
        )
        persisted, persisted_outcome = await self._persist(
            target, previous_state, previous_version
        )
        if persisted_outcome is not ApplyScoreOutcome.APPLIED:
            return ApplyScoreResult(persisted_outcome, persisted)
        if outcome is ApplyScoreOutcome.UNAVAILABLE and self.fanout is not None:
            await self.fanout.execute(
                post_id=job.post_id,
                version=persisted.scoring_processing_version,
                now=self.clock.utc_now(),
            )
        return ApplyScoreResult(outcome, persisted)

    async def _persist(
        self, target: Post, previous_state: ScoringState, previous_version: int
    ) -> tuple[Post, ApplyScoreOutcome]:
        try:
            persisted = await self.posts.update_scoring(
                ScoringPostUpdateRequest(
                    target,
                    expected_processing_version=previous_version,
                    expected_processing_state=previous_state,
                )
            )
            return persisted, ApplyScoreOutcome.APPLIED
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(
                target.post_id, as_of=self.clock.utc_now()
            )
            if current is not None and current.scoring_state is target.scoring_state:
                return current, ApplyScoreOutcome.IDEMPOTENT
            return target if current is None else current, ApplyScoreOutcome.CONFLICT

    async def _load_job(self, job_id: str, expected_version: int) -> AIJob:
        if type(job_id) is not str or not job_id or type(expected_version) is not int:
            raise ScoringTaskValidationError
        job = await self.jobs.get_by_id(job_id)
        if (
            job is None
            or job.version != expected_version
            or job.task_type != AITaskType.SCORING.value
            or job.prompt_version != "2.0.0"
            or job.schema_version != "2"
        ):
            raise ScoringTaskValidationError
        return job

    @staticmethod
    def _normalized_result(job: AIJob) -> AIResult:
        try:
            result = AIResult.model_validate(job.normalized_result)
        except ValidationError:
            raise ScoringTaskValidationError from None
        if (
            not result.success
            or result.task_type is not AITaskType.SCORING
            or result.prompt_version != job.prompt_version
            or result.schema_version != job.schema_version
            or result.result is None
        ):
            raise ScoringTaskValidationError
        return result


__all__ = (
    "ApplyAIScore",
    "ApplyScoreOutcome",
    "ApplyScoreResult",
    "ScoringFanout",
    "ScoringTaskValidationError",
)
