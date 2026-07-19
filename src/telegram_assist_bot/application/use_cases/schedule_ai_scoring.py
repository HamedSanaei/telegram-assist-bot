"""Schedule one durable delayed scoring Job from source publication time."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import (
    PostConcurrencyConflictError,
    ScoringPostUpdateRequest,
)
from telegram_assist_bot.domain.ai_task import AITaskType
from telegram_assist_bot.domain.scoring import ScoringState

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
    from telegram_assist_bot.application.ports import ScoringPostRepository
    from telegram_assist_bot.domain.ai_job import AIJob
    from telegram_assist_bot.domain.posts import Post
    from telegram_assist_bot.shared.config.models import ApplicationConfig

SCORING_PROMPT_VERSION = "2.0.0"
SCORING_SCHEMA_VERSION = "2"


class ScoringScheduleOutcome(StrEnum):
    """Describe a disabled, ineligible, created, or idempotent schedule."""

    DISABLED = "disabled"
    INELIGIBLE = "ineligible"
    SCHEDULED = "scheduled"
    ALREADY_SCHEDULED = "already_scheduled"
    CONFLICT = "conflict"


class ApprovalScoringBoundary(StrEnum):
    """Prove that scoring scheduling follows an approval application boundary."""

    READY = "approval_ready"
    DELIVERED = "approval_delivered"


@dataclass(frozen=True, slots=True)
class ScoringScheduleResult:
    """Return the durable Job only when scoring is scheduled."""

    outcome: ScoringScheduleOutcome
    job: AIJob | None = None
    post: Post | None = None


@dataclass(frozen=True, slots=True)
class ScheduleAIScoring:
    """Calculate the absolute due instant and enqueue exactly one logical Job."""

    config: ApplicationConfig = field(repr=False)
    posts: ScoringPostRepository = field(repr=False)
    enqueue_job: EnqueueAIJob = field(repr=False)

    async def execute(
        self, post: Post, *, boundary: ApprovalScoringBoundary | None
    ) -> ScoringScheduleResult:
        """Schedule without reading cache, reserving capacity, or calling a Provider."""
        if not self.config.features.ai_scoring_enabled:
            return ScoringScheduleResult(ScoringScheduleOutcome.DISABLED, post=post)
        scoring = self.config.scoring
        if scoring is None:
            raise ValueError("Enabled AI scoring requires explicit configuration.")
        if boundary is None or not post.scoring_is_eligible:
            return ScoringScheduleResult(ScoringScheduleOutcome.INELIGIBLE, post=post)

        due_at = post.source_published_at + timedelta(seconds=scoring.delay_seconds)
        job_id = self._job_id(post.post_id.value)
        if (
            post.scoring_state is ScoringState.SCHEDULED
            and post.scoring_job_id == job_id
        ):
            return ScoringScheduleResult(
                ScoringScheduleOutcome.ALREADY_SCHEDULED, post=post
            )
        enqueued = await self.enqueue_job.execute(
            post_id=post.post_id.value,
            task_type=AITaskType.SCORING.value,
            prompt_version=SCORING_PROMPT_VERSION,
            schema_version=SCORING_SCHEMA_VERSION,
            priority=10,
            max_attempts=self.config.ai.queue.max_attempts,
            job_id=job_id,
            next_run_at=due_at,
        )
        canonical_due = enqueued.job.next_run_at
        target = post.schedule_scoring(
            job_id=enqueued.job.job_id,
            due_at=canonical_due,
            expected_processing_version=post.scoring_processing_version,
        )
        try:
            persisted = await self.posts.update_scoring(
                ScoringPostUpdateRequest(
                    target,
                    expected_processing_version=post.scoring_processing_version,
                    expected_processing_state=post.scoring_state,
                )
            )
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(post.post_id, as_of=post.received_at)
            if (
                current is not None
                and current.scoring_job_id == enqueued.job.job_id
                and current.scoring_state is ScoringState.SCHEDULED
            ):
                return ScoringScheduleResult(
                    ScoringScheduleOutcome.ALREADY_SCHEDULED,
                    enqueued.job,
                    current,
                )
            return ScoringScheduleResult(
                ScoringScheduleOutcome.CONFLICT, enqueued.job, current
            )
        return ScoringScheduleResult(
            ScoringScheduleOutcome.SCHEDULED, enqueued.job, persisted
        )

    @staticmethod
    def _job_id(post_id: str) -> str:
        raw = (
            f"{post_id}|{AITaskType.SCORING.value}|"
            f"{SCORING_PROMPT_VERSION}|{SCORING_SCHEMA_VERSION}"
        ).encode()
        return f"job_score_{hashlib.sha256(raw).hexdigest()[:32]}"


__all__ = (
    "SCORING_PROMPT_VERSION",
    "SCORING_SCHEMA_VERSION",
    "ApprovalScoringBoundary",
    "ScheduleAIScoring",
    "ScoringScheduleOutcome",
    "ScoringScheduleResult",
)
