"""Isolated worker-facing seam for claimed delayed-scoring Jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ai.schemas import ScoringContext
from telegram_assist_bot.application.ports import ScoringPostUpdateRequest
from telegram_assist_bot.application.use_cases.apply_ai_score import (
    ApplyAIScore,
    ApplyScoreResult,
    ScoringTaskValidationError,
)
from telegram_assist_bot.domain.ai_job import AIJobStatus
from telegram_assist_bot.domain.ai_task import AITaskType
from telegram_assist_bot.domain.posts import PostId

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AIJobRepository,
        ScoringPostRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class ScoringHandler:
    """Gate Provider input before execution and delegate persisted completion."""

    posts: ScoringPostRepository = field(repr=False)
    jobs: AIJobRepository = field(repr=False)
    clock: Clock = field(repr=False)
    apply_score: ApplyAIScore = field(repr=False)

    async def prepare_claimed(
        self, *, job_id: str, expected_job_version: int, lease_owner: str
    ) -> ScoringContext | None:
        """Return only approved text, or stale/no-op before any Provider call."""
        job = await self.jobs.get_by_id(job_id)
        if (
            job is None
            or job.version != expected_job_version
            or job.status is not AIJobStatus.PROCESSING
            or job.task_type != AITaskType.SCORING.value
            or job.prompt_version != "2.0.0"
            or job.schema_version != "2"
            or job.lease_owner != lease_owner
        ):
            raise ScoringTaskValidationError
        post = await self.posts.get_by_id(
            PostId(job.post_id), as_of=self.clock.utc_now()
        )
        if post is None:
            expired = await self.posts.get_for_scoring_completion(PostId(job.post_id))
            if expired is not None and not expired.scoring_state.is_terminal:
                target = expired.mark_scoring_stale(
                    expected_processing_version=expired.scoring_processing_version
                )
                await self.posts.update_scoring(
                    ScoringPostUpdateRequest(
                        target,
                        expected_processing_version=expired.scoring_processing_version,
                        expected_processing_state=expired.scoring_state,
                    )
                )
            await self.jobs.update(job.expire(self.clock.utc_now()))
            return None
        text = post.original_content.text or post.original_content.caption
        if not text:
            await self.jobs.update(job.expire(self.clock.utc_now()))
            return None
        return ScoringContext(text=text)

    async def complete(
        self, *, job_id: str, expected_job_version: int
    ) -> ApplyScoreResult:
        """Persist one valid completed result."""
        return await self.apply_score.complete(
            job_id=job_id, expected_job_version=expected_job_version
        )

    async def fail(self, *, job_id: str, expected_job_version: int) -> ApplyScoreResult:
        """Apply configured durable retry or terminal unavailable behavior."""
        return await self.apply_score.fail(
            job_id=job_id, expected_job_version=expected_job_version
        )


__all__ = ("ScoringHandler",)
