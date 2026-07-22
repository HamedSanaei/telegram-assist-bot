"""Apply validated semantic duplicate results to one canonical Post."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from datetime import timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import ValidationError

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.schemas import SemanticDuplicateOutput
from telegram_assist_bot.application.ports import (
    PostConcurrencyConflictError,
    SemanticDuplicatePostUpdateRequest,
)
from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditEventType,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.domain.duplicates import (
    SemanticDuplicateFailure,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicatePolicy,
    SemanticDuplicateResult,
)
from telegram_assist_bot.domain.posts import PostId

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AIAuditRepository,
        AIJobRepository,
        SemanticDuplicateCandidateRepository,
        SemanticDuplicatePostRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.posts import Post


class SemanticDuplicateHandlerOutcome(StrEnum):
    """Describe one safe semantic handler outcome."""

    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    RETRY_SCHEDULED = "retry_scheduled"
    STALE = "stale"
    CONFLICT = "conflict"


class SemanticDuplicateTaskValidationError(Exception):
    """Reject malformed or inconsistent task data using a safe reason code."""

    def __init__(self, reason_code: str = "invalid_semantic_task") -> None:
        """Retain only a bounded stable reason code, never raw task data."""
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class SemanticDuplicateHandlingResult:
    """Return canonical Post state and optional Audit side-effect status."""

    outcome: SemanticDuplicateHandlerOutcome
    post: Post | None
    audit_persisted: bool | None


def validate_semantic_consistency(
    output: SemanticDuplicateOutput, threshold: float
) -> None:
    """Reject Boolean/similarity disagreement without rewriting either value."""
    if output.is_duplicate is not (output.similarity >= threshold):
        raise SemanticDuplicateTaskValidationError("inconsistent_similarity_boolean")


@dataclass(frozen=True, slots=True)
class SemanticDuplicateHandler:
    """Select the deterministic best eligible candidate and persist one result."""

    posts: SemanticDuplicatePostRepository = field(repr=False)
    ai_jobs: AIJobRepository = field(repr=False)
    candidates: SemanticDuplicateCandidateRepository = field(repr=False)
    clock: Clock = field(repr=False)
    audit: AIAuditRepository | None = field(default=None, repr=False)

    async def complete(
        self,
        *,
        job_id: str,
        expected_job_version: int,
        threshold: float,
        duplicate_policy: SemanticDuplicatePolicy,
    ) -> SemanticDuplicateHandlingResult:
        """Apply highest similarity; ties follow deterministic candidate order."""
        if type(threshold) is not float or not 0.0 <= threshold <= 1.0:
            raise SemanticDuplicateTaskValidationError("invalid_threshold")
        if type(duplicate_policy) is not SemanticDuplicatePolicy:
            raise SemanticDuplicateTaskValidationError("invalid_duplicate_policy")
        job = await self._load_job(job_id, expected_job_version)
        if (
            job.status is not AIJobStatus.COMPLETED
            or not job.semantic_candidate_results
        ):
            raise SemanticDuplicateTaskValidationError
        now = self.clock.utc_now()
        post = await self.posts.get_by_id(PostId(job.post_id), as_of=now)
        if post is None:
            return SemanticDuplicateHandlingResult(
                SemanticDuplicateHandlerOutcome.STALE, None, None
            )
        if post.semantic_duplicate_state.is_terminal:
            return SemanticDuplicateHandlingResult(
                SemanticDuplicateHandlerOutcome.IDEMPOTENT, post, None
            )
        eligible = await self.candidates.list_candidates(
            current_post_id=post.post_id,
            now=now,
            window_start=now - timedelta(days=14),
            limit=100,
        )
        order = {item.post_id.value: index for index, item in enumerate(eligible)}
        evaluations: list[tuple[int, PostId, AIResult, SemanticDuplicateOutput]] = []
        for raw in job.semantic_candidate_results:
            if not isinstance(raw, dict):
                raise SemanticDuplicateTaskValidationError
            candidate_value = raw.get("candidate_post_id")
            if not isinstance(candidate_value, str) or candidate_value not in order:
                raise SemanticDuplicateTaskValidationError(
                    "unknown_candidate_reference"
                )
            try:
                ai_result = AIResult.model_validate(raw.get("result"))
                output = SemanticDuplicateOutput.model_validate(ai_result.result)
            except ValidationError:
                raise SemanticDuplicateTaskValidationError from None
            if (
                ai_result.task_type is not AITaskType.SEMANTIC_DUPLICATE
                or ai_result.prompt_version != job.prompt_version
                or ai_result.schema_version != job.schema_version
                or ai_result.confidence != output.confidence
                or ai_result.reason != output.reason
            ):
                raise SemanticDuplicateTaskValidationError
            validate_semantic_consistency(output, threshold)
            evaluations.append(
                (order[candidate_value], PostId(candidate_value), ai_result, output)
            )
        duplicates = [item for item in evaluations if item[3].is_duplicate]
        if not evaluations:
            raise SemanticDuplicateTaskValidationError
        _, candidate_id, ai_result, output = min(
            duplicates or evaluations,
            key=lambda item: (-item[3].similarity, item[0]),
        )
        result = SemanticDuplicateResult(
            is_duplicate=output.is_duplicate,
            similarity=output.similarity,
            confidence=output.confidence,
            matched_post_id=candidate_id if output.is_duplicate else None,
            reason=output.reason,
            provider_name=ai_result.provider_name,
            model_name=ai_result.model_name,
            checked_at=ai_result.created_at,
            prompt_version=ai_result.prompt_version,
            schema_version=ai_result.schema_version,
            attempt_number=ai_result.attempt_number,
            fallback_count=ai_result.fallback_count,
            cache_hit=ai_result.cache_hit,
            cache_age_seconds=ai_result.cache_age_seconds,
        )
        prior_state = post.semantic_duplicate_state
        prior_version = post.semantic_duplicate_version
        target = post.apply_semantic_duplicate_result(
            result,
            policy=duplicate_policy,
            job_id=job.job_id,
            expected_processing_version=prior_version,
        )
        try:
            persisted = await self.posts.update_semantic_duplicate(
                SemanticDuplicatePostUpdateRequest(target, prior_version, prior_state)
            )
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(post.post_id, as_of=now)
            if current is not None and current.semantic_duplicate_result == result:
                return SemanticDuplicateHandlingResult(
                    SemanticDuplicateHandlerOutcome.IDEMPOTENT, current, None
                )
            return SemanticDuplicateHandlingResult(
                SemanticDuplicateHandlerOutcome.CONFLICT, current, None
            )
        return SemanticDuplicateHandlingResult(
            SemanticDuplicateHandlerOutcome.APPLIED,
            persisted,
            await self._audit(job, result),
        )

    async def fail(
        self,
        *,
        job_id: str,
        expected_job_version: int,
        policy: SemanticDuplicateFailurePolicy,
    ) -> SemanticDuplicateHandlingResult:
        """Apply T039 retry/final failure without fabricating a non-match."""
        job = await self._load_job(job_id, expected_job_version)
        retry = policy is SemanticDuplicateFailurePolicy.RETRY_LATER
        required = (
            AIJobStatus.WAITING_FOR_RETRY if retry else AIJobStatus.ALL_PROVIDERS_FAILED
        )
        if job.status is not required or job.updated_at is None:
            raise SemanticDuplicateTaskValidationError
        now = self.clock.utc_now()
        post = await self.posts.get_by_id(PostId(job.post_id), as_of=now)
        if post is None or post.semantic_duplicate_state.is_terminal:
            return SemanticDuplicateHandlingResult(
                SemanticDuplicateHandlerOutcome.STALE, post, None
            )
        failure = SemanticDuplicateFailure(
            policy=policy,
            failure_category=job.safe_last_failure_code or "unknown",
            failed_at=job.updated_at,
            next_retry_at=job.next_run_at if retry else None,
        )
        prior_state = post.semantic_duplicate_state
        prior_version = post.semantic_duplicate_version
        target = post.apply_semantic_duplicate_failure(
            failure,
            job_id=job.job_id,
            expected_processing_version=prior_version,
        )
        try:
            persisted = await self.posts.update_semantic_duplicate(
                SemanticDuplicatePostUpdateRequest(target, prior_version, prior_state)
            )
        except PostConcurrencyConflictError:
            return SemanticDuplicateHandlingResult(
                SemanticDuplicateHandlerOutcome.CONFLICT, post, None
            )
        return SemanticDuplicateHandlingResult(
            SemanticDuplicateHandlerOutcome.RETRY_SCHEDULED
            if retry
            else SemanticDuplicateHandlerOutcome.APPLIED,
            persisted,
            None,
        )

    async def _load_job(self, job_id: str, version: int) -> AIJob:
        job = await self.ai_jobs.get_by_id(job_id)
        if (
            job is None
            or job.version != version
            or job.task_type != AITaskType.SEMANTIC_DUPLICATE.value
        ):
            raise SemanticDuplicateTaskValidationError
        return job

    async def _audit(self, job: AIJob, result: SemanticDuplicateResult) -> bool | None:
        if self.audit is None:
            return None
        identity = f"{job.job_id}|{job.version}|semantic-result"
        event = AIAuditEvent(
            event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            event_type=AIAuditEventType.SEMANTIC_DUPLICATE_RESULT_APPLIED,
            job_id=job.job_id,
            post_id=job.post_id,
            task_type=AITaskType.SEMANTIC_DUPLICATE,
            prompt_version=job.prompt_version,
            schema_version=job.schema_version,
            occurred_at=self.clock.utc_now(),
            provider_name=result.provider_name,
            model_name=result.model_name,
            success=True,
            cache_hit=result.cache_hit,
        )
        try:
            return await self.audit.append(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return False


__all__ = (
    "SemanticDuplicateHandler",
    "SemanticDuplicateHandlerOutcome",
    "SemanticDuplicateHandlingResult",
    "SemanticDuplicateTaskValidationError",
    "validate_semantic_consistency",
)
