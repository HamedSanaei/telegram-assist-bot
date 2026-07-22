"""Enqueue one isolated semantic duplicate job after exact detection passes."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ai.schemas import SemanticDuplicateContext
from telegram_assist_bot.application.ports import (
    EnqueueJobOutcome,
    PostConcurrencyConflictError,
    SemanticDuplicatePostUpdateRequest,
)
from telegram_assist_bot.application.text_normalization import normalize_exact_text
from telegram_assist_bot.domain.duplicates import (
    DuplicateCheckResult,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
    SemanticDuplicateState,
)
from telegram_assist_bot.domain.posts import Post, PostStatus
from telegram_assist_bot.shared.errors import ConfigurationError

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
    from telegram_assist_bot.application.ports import (
        AIJobRepository,
        SemanticDuplicateCandidate,
        SemanticDuplicateCandidateRepository,
        SemanticDuplicatePostRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.ai_job import AIJob

_SEMANTIC_PROMPT_VERSION = "2.0.0"
_CANDIDATE_LIMIT = 100


class SemanticDuplicateEnqueueOutcome(StrEnum):
    """Describe disabled, short-circuited, empty and durable enqueue outcomes."""

    DISABLED = "disabled"
    EXACT_DUPLICATE = "exact_duplicate"
    NO_CANDIDATES = "no_candidates"
    ENQUEUED = "enqueued"
    ALREADY_ENQUEUED = "already_enqueued"


class SemanticDuplicatePrerequisiteError(Exception):
    """Reject semantic work before advertisement and exact checks permit it."""


@dataclass(frozen=True, slots=True)
class SemanticCandidateInvocation:
    """Map an application-owned candidate to a Provider-safe pairwise context."""

    candidate: SemanticDuplicateCandidate = field(repr=False)
    context: SemanticDuplicateContext = field(repr=False)


@dataclass(frozen=True, slots=True)
class SemanticDuplicateEnqueueResult:
    """Return the canonical job and deterministic Provider-safe invocations."""

    outcome: SemanticDuplicateEnqueueOutcome
    job: AIJob | None = None
    invocations: tuple[SemanticCandidateInvocation, ...] = field(default=(), repr=False)


@dataclass(frozen=True, slots=True)
class DetectSemanticDuplicate:
    """Prepare pairwise contexts and enqueue one logical semantic AIJob."""

    ai_jobs: AIJobRepository = field(repr=False)
    posts: SemanticDuplicatePostRepository = field(repr=False)
    candidates: SemanticDuplicateCandidateRepository = field(repr=False)
    prompt_registry: PromptRegistry = field(repr=False)
    clock: Clock = field(repr=False)

    async def execute(
        self,
        post: Post,
        *,
        exact_result: DuplicateCheckResult | None,
        global_enabled: bool,
        source_enabled: bool | None,
        threshold: float | None,
        duplicate_policy: SemanticDuplicatePolicy | None,
        failure_policy: SemanticDuplicateFailurePolicy | None,
        priority: int = 30,
        max_attempts: int = 3,
    ) -> SemanticDuplicateEnqueueResult:
        """Short-circuit exact matches and preserve T016 normalization exactly."""
        if not global_enabled or source_enabled is False:
            return SemanticDuplicateEnqueueResult(
                SemanticDuplicateEnqueueOutcome.DISABLED
            )
        if (
            source_enabled is None
            or type(threshold) is not float
            or not 0.0 <= threshold <= 1.0
            or type(duplicate_policy) is not SemanticDuplicatePolicy
            or type(failure_policy) is not SemanticDuplicateFailurePolicy
        ):
            raise ConfigurationError(
                cause=ValueError("semantic duplicate policy is incomplete")
            )
        if exact_result is None or type(post) is not Post:
            raise SemanticDuplicatePrerequisiteError
        if exact_result.is_duplicate:
            return SemanticDuplicateEnqueueResult(
                SemanticDuplicateEnqueueOutcome.EXACT_DUPLICATE
            )
        if (
            post.status is not PostStatus.STORED
            or not post.advertisement_allows_next_stage
        ):
            raise SemanticDuplicatePrerequisiteError
        source = (
            post.original_text
            if post.original_text is not None
            else post.original_caption
        )
        normalized = normalize_exact_text(source)
        if normalized is None or not normalized or normalized.isspace():
            raise SemanticDuplicatePrerequisiteError
        now = self.clock.utc_now()
        candidates = await self.candidates.list_candidates(
            current_post_id=post.post_id,
            now=now,
            window_start=now - timedelta(days=14),
            limit=_CANDIDATE_LIMIT,
        )
        invocations = tuple(
            SemanticCandidateInvocation(
                candidate=item,
                context=SemanticDuplicateContext(
                    text=normalized,
                    compare_text=item.comparison_text,
                    similarity_threshold=threshold,
                ),
            )
            for item in candidates
        )
        if not invocations:
            return SemanticDuplicateEnqueueResult(
                SemanticDuplicateEnqueueOutcome.NO_CANDIDATES
            )
        prompt = self.prompt_registry.get_prompt(
            AITaskType.SEMANTIC_DUPLICATE, _SEMANTIC_PROMPT_VERSION
        )
        canonical = await EnqueueAIJob(self.ai_jobs, self.clock).execute(
            post_id=post.post_id.value,
            task_type=AITaskType.SEMANTIC_DUPLICATE.value,
            prompt_version=prompt.prompt_version,
            schema_version=prompt.schema_version,
            priority=priority,
            max_attempts=max_attempts,
        )
        if (
            post.semantic_duplicate_state is SemanticDuplicateState.PENDING
            and post.semantic_duplicate_job_id == canonical.job.job_id
        ):
            return SemanticDuplicateEnqueueResult(
                SemanticDuplicateEnqueueOutcome.ALREADY_ENQUEUED,
                canonical.job,
                invocations,
            )
        target = post.start_semantic_duplicate_check(
            job_id=canonical.job.job_id,
            expected_processing_version=post.semantic_duplicate_version,
            requested_at=now,
        )
        try:
            await self.posts.update_semantic_duplicate(
                SemanticDuplicatePostUpdateRequest(
                    post=target,
                    expected_processing_version=post.semantic_duplicate_version,
                    expected_processing_state=post.semantic_duplicate_state,
                )
            )
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(post.post_id, as_of=now)
            if not (
                current is not None
                and current.semantic_duplicate_state is SemanticDuplicateState.PENDING
                and current.semantic_duplicate_job_id == canonical.job.job_id
            ):
                raise
        outcome = (
            SemanticDuplicateEnqueueOutcome.ENQUEUED
            if canonical.outcome is EnqueueJobOutcome.CREATED
            else SemanticDuplicateEnqueueOutcome.ALREADY_ENQUEUED
        )
        return SemanticDuplicateEnqueueResult(outcome, canonical.job, invocations)


__all__ = (
    "DetectSemanticDuplicate",
    "SemanticCandidateInvocation",
    "SemanticDuplicateEnqueueOutcome",
    "SemanticDuplicateEnqueueResult",
    "SemanticDuplicatePrerequisiteError",
)
