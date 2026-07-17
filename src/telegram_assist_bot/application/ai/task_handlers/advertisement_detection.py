"""Apply completed advertisement AI Jobs to canonical Post processing state."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import ValidationError

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionOutput
from telegram_assist_bot.application.ports import (
    AdvertisementPostUpdateRequest,
    PostConcurrencyConflictError,
)
from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditEventType,
)
from telegram_assist_bot.domain.advertisement import (
    AdvertisementCheckFailure,
    AdvertisementCheckResult,
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
    InvalidAdvertisementResultError,
    InvalidAdvertisementTransitionError,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.domain.posts import InvalidPostIdentifierError, PostId

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AdvertisementPostRepository,
        AIJobRepository,
    )
    from telegram_assist_bot.application.ports.ai_audit_repository import (
        AIAuditRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.posts import Post


class AdvertisementHandlerOutcome(StrEnum):
    """Describe one idempotent processing outcome without storage details."""

    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    RETRY_SCHEDULED = "retry_scheduled"
    STALE = "stale"
    CONFLICT = "conflict"


class AdvertisementTaskValidationError(Exception):
    """Reject mismatched or incomplete persisted task data safely."""

    def __init__(self) -> None:
        """Avoid retaining raw payloads or validation details."""
        super().__init__("Persisted advertisement task data is invalid.")


@dataclass(frozen=True, slots=True)
class AdvertisementHandlingResult:
    """Return the canonical state and observable audit side-effect outcome."""

    outcome: AdvertisementHandlerOutcome
    post: Post | None
    audit_persisted: bool | None


@dataclass(frozen=True, slots=True)
class AdvertisementDetectionHandler:
    """Map one normalized task result or final failure through Post CAS."""

    posts: AdvertisementPostRepository = field(repr=False)
    ai_jobs: AIJobRepository = field(repr=False)
    clock: Clock = field(repr=False)
    audit: AIAuditRepository | None = field(default=None, repr=False)

    async def complete(
        self,
        *,
        job_id: str,
        expected_job_version: int,
    ) -> AdvertisementHandlingResult:
        """Apply one validated normalized result, including a valid cache hit."""
        job = await self._load_job(job_id, expected_job_version)
        if job.status is not AIJobStatus.COMPLETED or job.normalized_result is None:
            raise AdvertisementTaskValidationError
        ai_result = self._normalized_result(job)
        result = self._advertisement_result(ai_result)
        post = await self.posts.get_by_id(
            self._post_id(job),
            as_of=self.clock.utc_now(),
        )
        if post is None:
            return AdvertisementHandlingResult(
                AdvertisementHandlerOutcome.STALE,
                None,
                None,
            )
        if post.advertisement_state.is_terminal:
            if (
                post.advertisement_job_id == job.job_id
                and post.advertisement_result == result
            ):
                return AdvertisementHandlingResult(
                    AdvertisementHandlerOutcome.IDEMPOTENT,
                    post,
                    None,
                )
            return AdvertisementHandlingResult(
                AdvertisementHandlerOutcome.STALE,
                post,
                None,
            )
        previous_state = post.advertisement_state
        previous_version = post.advertisement_processing_version
        try:
            target = post.apply_advertisement_result(
                result,
                job_id=job.job_id,
                expected_processing_version=previous_version,
            )
        except InvalidAdvertisementTransitionError:
            return AdvertisementHandlingResult(
                AdvertisementHandlerOutcome.STALE,
                post,
                None,
            )
        persisted, outcome = await self._persist_or_resolve(
            target,
            previous_state,
            previous_version,
            job,
        )
        if outcome is not AdvertisementHandlerOutcome.APPLIED:
            return AdvertisementHandlingResult(outcome, persisted, None)
        audit_persisted = await self._append_audit(
            job,
            AIAuditEventType.ADVERTISEMENT_RESULT_APPLIED,
            success=True,
            failure_category=None,
            provider_name=result.provider_name,
            model_name=result.model_name,
            cache_hit=result.cache_hit,
        )
        return AdvertisementHandlingResult(outcome, persisted, audit_persisted)

    async def fail(
        self,
        *,
        job_id: str,
        expected_job_version: int,
        policy: AdvertisementFailurePolicy,
    ) -> AdvertisementHandlingResult:
        """Apply exactly one approved final-failure or future-retry policy."""
        if type(policy) is not AdvertisementFailurePolicy:
            raise AdvertisementTaskValidationError
        job = await self._load_job(job_id, expected_job_version)
        retry_later = policy is AdvertisementFailurePolicy.RETRY_LATER
        required_status = (
            AIJobStatus.WAITING_FOR_RETRY
            if retry_later
            else AIJobStatus.ALL_PROVIDERS_FAILED
        )
        if job.status is not required_status:
            raise AdvertisementTaskValidationError
        post = await self.posts.get_by_id(
            self._post_id(job),
            as_of=self.clock.utc_now(),
        )
        if post is None:
            return AdvertisementHandlingResult(
                AdvertisementHandlerOutcome.STALE,
                None,
                None,
            )
        if post.advertisement_state.is_terminal:
            if (
                post.advertisement_job_id == job.job_id
                and post.advertisement_failure is not None
                and post.advertisement_failure.policy is policy
            ):
                return AdvertisementHandlingResult(
                    AdvertisementHandlerOutcome.IDEMPOTENT,
                    post,
                    None,
                )
            return AdvertisementHandlingResult(
                AdvertisementHandlerOutcome.STALE,
                post,
                None,
            )
        if job.updated_at is None:
            raise AdvertisementTaskValidationError
        failure = AdvertisementCheckFailure(
            policy=policy,
            failure_category=job.safe_last_failure_code or "unknown",
            failure_type="all_providers_failed",
            failed_at=job.updated_at,
            attempted_candidates_count=job.attempted_candidates_count or 0,
            retry_count=job.retry_count or 0,
            fallback_count=job.fallback_count or 0,
            next_retry_at=job.next_run_at if retry_later else None,
        )
        previous_state = post.advertisement_state
        previous_version = post.advertisement_processing_version
        try:
            target = post.apply_advertisement_failure(
                failure,
                job_id=job.job_id,
                expected_processing_version=previous_version,
            )
        except InvalidAdvertisementTransitionError:
            return AdvertisementHandlingResult(
                AdvertisementHandlerOutcome.STALE,
                post,
                None,
            )
        persisted, outcome = await self._persist_or_resolve(
            target,
            previous_state,
            previous_version,
            job,
        )
        if outcome is not AdvertisementHandlerOutcome.APPLIED:
            return AdvertisementHandlingResult(outcome, persisted, None)
        audit_persisted = await self._append_audit(
            job,
            AIAuditEventType.ADVERTISEMENT_FAILURE_POLICY_APPLIED,
            success=False,
            failure_category=failure.failure_category,
            provider_name=None,
            model_name=None,
            cache_hit=False,
        )
        effective_outcome = (
            AdvertisementHandlerOutcome.RETRY_SCHEDULED
            if retry_later
            else AdvertisementHandlerOutcome.APPLIED
        )
        return AdvertisementHandlingResult(
            effective_outcome,
            persisted,
            audit_persisted,
        )

    async def _load_job(self, job_id: str, expected_version: int) -> AIJob:
        if (
            type(job_id) is not str
            or not job_id
            or type(expected_version) is not int
            or expected_version < 0
        ):
            raise AdvertisementTaskValidationError
        job = await self.ai_jobs.get_by_id(job_id)
        if (
            job is None
            or job.version != expected_version
            or job.task_type != AITaskType.ADVERTISEMENT_DETECTION.value
        ):
            raise AdvertisementTaskValidationError
        return job

    @staticmethod
    def _post_id(job: AIJob) -> PostId:
        try:
            return PostId(job.post_id)
        except InvalidPostIdentifierError:
            raise AdvertisementTaskValidationError from None

    @staticmethod
    def _normalized_result(job: AIJob) -> AIResult:
        try:
            result = AIResult.model_validate(job.normalized_result)
        except ValidationError:
            raise AdvertisementTaskValidationError from None
        if (
            not result.success
            or result.task_type is not AITaskType.ADVERTISEMENT_DETECTION
            or result.prompt_version != job.prompt_version
            or result.schema_version != job.schema_version
        ):
            raise AdvertisementTaskValidationError
        return result

    @staticmethod
    def _advertisement_result(ai_result: AIResult) -> AdvertisementCheckResult:
        try:
            output = AdvertisementDetectionOutput.model_validate(ai_result.result)
            if (
                ai_result.confidence != output.confidence
                or ai_result.reason != output.reason
            ):
                raise AdvertisementTaskValidationError
            return AdvertisementCheckResult(
                is_advertisement=output.is_advertisement,
                confidence=output.confidence,
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
        except (InvalidAdvertisementResultError, ValidationError):
            raise AdvertisementTaskValidationError from None

    async def _persist_or_resolve(
        self,
        target: Post,
        previous_state: AdvertisementProcessingState,
        previous_version: int,
        job: AIJob,
    ) -> tuple[Post, AdvertisementHandlerOutcome]:
        try:
            persisted = await self.posts.update_advertisement(
                AdvertisementPostUpdateRequest(
                    post=target,
                    expected_processing_version=previous_version,
                    expected_processing_state=previous_state,
                )
            )
            return persisted, AdvertisementHandlerOutcome.APPLIED
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(
                self._post_id(job),
                as_of=self.clock.utc_now(),
            )
            if current is None:
                return target, AdvertisementHandlerOutcome.STALE
            if (
                current.advertisement_job_id == target.advertisement_job_id
                and current.advertisement_state is target.advertisement_state
                and current.advertisement_result == target.advertisement_result
                and current.advertisement_failure == target.advertisement_failure
            ):
                return current, AdvertisementHandlerOutcome.IDEMPOTENT
            return current, AdvertisementHandlerOutcome.CONFLICT

    async def _append_audit(
        self,
        job: AIJob,
        event_type: AIAuditEventType,
        *,
        success: bool,
        failure_category: str | None,
        provider_name: str | None,
        model_name: str | None,
        cache_hit: bool,
    ) -> bool | None:
        if self.audit is None:
            return None
        identity = f"{job.job_id}|{job.version}|{event_type.value}"
        event = AIAuditEvent(
            event_id=hashlib.sha256(identity.encode("utf-8")).hexdigest(),
            event_type=event_type,
            job_id=job.job_id,
            post_id=job.post_id,
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version=job.prompt_version,
            schema_version=job.schema_version,
            occurred_at=self.clock.utc_now(),
            provider_name=provider_name,
            model_name=model_name,
            success=success,
            failure_category=failure_category,
            cache_hit=cache_hit,
        )
        try:
            return await self.audit.append(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            return False


__all__ = (
    "AdvertisementDetectionHandler",
    "AdvertisementHandlerOutcome",
    "AdvertisementHandlingResult",
    "AdvertisementTaskValidationError",
)
