"""Isolated application use case for enqueueing advertisement detection."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionContext
from telegram_assist_bot.application.ports import (
    AdvertisementPostUpdateRequest,
    EnqueueJobOutcome,
    PostConcurrencyConflictError,
)
from telegram_assist_bot.domain.advertisement import (
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.ai_job import AIJobPriority
from telegram_assist_bot.domain.posts import Post, PostStatus
from telegram_assist_bot.shared.errors import ConfigurationError

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
    from telegram_assist_bot.application.ports import (
        AdvertisementPostRepository,
        AIJobRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.ai_job import AIJob

_ADVERTISEMENT_PROMPT_VERSION = "1.0.0"


class AdvertisementEnqueueOutcome(StrEnum):
    """Describe disabled, new, and idempotently existing detection work."""

    DISABLED = "disabled"
    ENQUEUED = "enqueued"
    ALREADY_ENQUEUED = "already_enqueued"


class AdvertisementInputUnavailableError(Exception):
    """Report that no approved text or caption exists for detection."""


@dataclass(frozen=True, slots=True)
class AdvertisementEnqueueResult:
    """Return the canonical job and exact unnormalized request context."""

    outcome: AdvertisementEnqueueOutcome
    job: AIJob | None = None
    request_context: AdvertisementDetectionContext | None = field(
        default=None,
        repr=False,
    )


@dataclass(frozen=True, slots=True)
class DetectAdvertisement:
    """Enqueue one canonical advertisement task without calling a Provider."""

    ai_jobs: AIJobRepository = field(repr=False)
    posts: AdvertisementPostRepository = field(repr=False)
    prompt_registry: PromptRegistry = field(repr=False)
    clock: Clock = field(repr=False)

    async def execute(
        self,
        post: Post,
        *,
        global_enabled: bool,
        source_enabled: bool | None,
        failure_policy: AdvertisementFailurePolicy | None,
        priority: int = AIJobPriority.HIGH,
        max_attempts: int = 3,
    ) -> AdvertisementEnqueueResult:
        """Respect both feature flags and persist one pending Post state."""
        if not global_enabled or source_enabled is False:
            return AdvertisementEnqueueResult(AdvertisementEnqueueOutcome.DISABLED)
        if (
            source_enabled is None
            or type(failure_policy) is not AdvertisementFailurePolicy
        ):
            raise ConfigurationError(
                cause=ValueError("advertisement detection policy is incomplete")
            )
        if type(post) is not Post or post.status is not PostStatus.STORED:
            raise AdvertisementInputUnavailableError
        source_text = post.original_text
        if source_text is None:
            source_text = post.original_caption
        if source_text is None or not source_text:
            raise AdvertisementInputUnavailableError
        prompt = self.prompt_registry.get_prompt(
            AITaskType.ADVERTISEMENT_DETECTION,
            _ADVERTISEMENT_PROMPT_VERSION,
        )
        context = AdvertisementDetectionContext(text=source_text)
        enqueue = EnqueueAIJob(self.ai_jobs, self.clock)
        canonical = await enqueue.execute(
            post_id=post.post_id.value,
            task_type=AITaskType.ADVERTISEMENT_DETECTION.value,
            prompt_version=prompt.prompt_version,
            schema_version=prompt.schema_version,
            priority=priority,
            max_attempts=max_attempts,
        )
        if (
            post.advertisement_state is AdvertisementProcessingState.PENDING
            and post.advertisement_job_id == canonical.job.job_id
        ):
            return AdvertisementEnqueueResult(
                AdvertisementEnqueueOutcome.ALREADY_ENQUEUED,
                canonical.job,
                context,
            )
        target = post.start_advertisement_check(
            job_id=canonical.job.job_id,
            expected_processing_version=post.advertisement_processing_version,
            requested_at=self.clock.utc_now(),
        )
        try:
            await self.posts.update_advertisement(
                AdvertisementPostUpdateRequest(
                    post=target,
                    expected_processing_version=post.advertisement_processing_version,
                    expected_processing_state=post.advertisement_state,
                )
            )
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(
                post.post_id,
                as_of=self.clock.utc_now(),
            )
            if not (
                current is not None
                and current.advertisement_state is AdvertisementProcessingState.PENDING
                and current.advertisement_job_id == canonical.job.job_id
            ):
                raise
        outcome = (
            AdvertisementEnqueueOutcome.ENQUEUED
            if canonical.outcome is EnqueueJobOutcome.CREATED
            else AdvertisementEnqueueOutcome.ALREADY_ENQUEUED
        )
        return AdvertisementEnqueueResult(outcome, canonical.job, context)


__all__ = (
    "AdvertisementEnqueueOutcome",
    "AdvertisementEnqueueResult",
    "AdvertisementInputUnavailableError",
    "DetectAdvertisement",
)
