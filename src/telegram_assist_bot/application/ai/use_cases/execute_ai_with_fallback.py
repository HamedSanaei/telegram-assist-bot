"""Use case for executing AI tasks with config-driven routing, retry, and fallback."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import replace
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from telegram_assist_bot.application.ai.cache_key import build_ai_cache_identity
from telegram_assist_bot.application.ai.contracts import (
    AISideEffectWarningCode,
    AITaskType,
)
from telegram_assist_bot.application.ai.provider_guard import (
    AllProvidersTemporarilyUnavailableError,
    ProviderAttemptGuard,
    ProviderTemporarilyUnavailableError,
)
from telegram_assist_bot.application.ai.response_normalizer import ResponseNormalizer
from telegram_assist_bot.application.ai.response_parser import ResponseParser
from telegram_assist_bot.application.ai.response_validator import ResponseValidator
from telegram_assist_bot.application.ai.retry import execute_candidate_with_retry
from telegram_assist_bot.application.ai.routing import select_route_candidates
from telegram_assist_bot.application.ports import AIJobNotFoundError
from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditEventType,
)
from telegram_assist_bot.application.ports.ai_cache_repository import AICacheEntry
from telegram_assist_bot.application.ports.provider_metrics_repository import (
    ProviderMetricDelta,
)
from telegram_assist_bot.domain.ai_job import AIJobStatus
from telegram_assist_bot.shared.errors import ConfigurationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import BaseModel

    from telegram_assist_bot.application.ai.contracts import AIResult
    from telegram_assist_bot.application.ai.retry import AsyncSleeper, JitterSource
    from telegram_assist_bot.application.ports import AIJobRepository, AIProvider
    from telegram_assist_bot.application.ports.ai_audit_repository import (
        AIAuditRepository,
    )
    from telegram_assist_bot.application.ports.ai_cache_repository import (
        AICacheRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.application.ports.provider_metrics_repository import (
        ProviderMetricsRepository,
    )
    from telegram_assist_bot.shared.config import (
        AiConfig,
        AiRouteCandidateConfig,
        AiTaskFailureAction,
    )


class AllProvidersFailedError(Exception):
    """Exception raised when all configured AI providers/models fail."""

    def __init__(self, action: AiTaskFailureAction, message: str) -> None:
        """Initialize the error with the failure action."""
        self.action = action
        super().__init__(message)


class ExecuteAIWithFallback:
    """Orchestrates AI task execution across fallback routes and retries."""

    def __init__(
        self,
        config: AiConfig,
        providers_by_name: Mapping[str, AIProvider],
        ai_job_repository: AIJobRepository,
        clock: Clock,
        sleeper: AsyncSleeper,
        jitter_source: JitterSource,
        provider_guard: ProviderAttemptGuard,
        cache_repository: AICacheRepository | None = None,
        audit_repository: AIAuditRepository | None = None,
        metrics_repository: ProviderMetricsRepository | None = None,
    ) -> None:
        """Initialize the orchestrator."""
        self.config = config
        self.providers_by_name = providers_by_name
        self.ai_job_repository = ai_job_repository
        self.clock = clock
        self.sleeper = sleeper
        self.jitter_source = jitter_source
        self.provider_guard = provider_guard
        self.cache_repository = cache_repository
        self.audit_repository = audit_repository
        self.metrics_repository = metrics_repository

    async def _append_audit(
        self,
        *,
        event_type: AIAuditEventType,
        job_id: str,
        post_id: str,
        task_type: AITaskType,
        prompt_version: str,
        schema_version: str,
        warnings: list[AISideEffectWarningCode],
        sequence_number: int = 0,
        provider_name: str | None = None,
        model_name: str | None = None,
        attempt_number: int | None = None,
        retry_count: int = 0,
        fallback_count: int = 0,
        success: bool | None = None,
        failure_category: str | None = None,
        http_status: int | None = None,
        latency_seconds: float | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cache_hit: bool = False,
    ) -> None:
        """Persist one sanitized event without invalidating a valid AI result."""
        audit_config = self.config.audit
        if not audit_config.enabled:
            return
        if self.audit_repository is None:
            warning = AISideEffectWarningCode.AUDIT_REPOSITORY_UNAVAILABLE
            if warning not in warnings:
                warnings.append(warning)
            return
        identity = "|".join(
            (
                job_id,
                str(sequence_number),
                event_type.value,
                provider_name or "",
                model_name or "",
            )
        )
        event_id = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        now = self.clock.utc_now()
        retention = audit_config.retention_seconds
        expires_at = now + timedelta(seconds=retention) if retention else None
        event = AIAuditEvent(
            event_id=event_id,
            event_type=event_type,
            job_id=job_id,
            post_id=post_id,
            task_type=task_type,
            prompt_version=prompt_version,
            schema_version=schema_version,
            occurred_at=now,
            provider_name=provider_name,
            model_name=model_name,
            sequence_number=sequence_number or None,
            attempt_number=attempt_number,
            retry_count=retry_count,
            fallback_count=fallback_count,
            success=success,
            failure_category=failure_category,
            http_status=http_status,
            latency_seconds=latency_seconds,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_hit=cache_hit,
            expires_at=expires_at,
        )
        try:
            await self.audit_repository.append(event)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            warning = AISideEffectWarningCode.AUDIT_APPEND_FAILED
            if warning not in warnings:
                warnings.append(warning)

    async def _increment_metrics(
        self,
        *,
        provider_name: str,
        model_name: str,
        delta: ProviderMetricDelta,
        warnings: list[AISideEffectWarningCode],
    ) -> None:
        """Apply one atomic metric delta as a bounded best-effort side effect."""
        if self.metrics_repository is None:
            return
        try:
            await self.metrics_repository.increment(provider_name, model_name, delta)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            warning = AISideEffectWarningCode.METRICS_INCREMENT_FAILED
            if warning not in warnings:
                warnings.append(warning)

    async def execute(
        self,
        job_id: str,
        owner: str,
        prompt_text: str,
        request_context: BaseModel,
        language: str = "und",
    ) -> AIResult:
        """Executes the AI task routing, retrying, and falling back as configured."""
        # 1. Fetch and validate job
        job = await self.ai_job_repository.get_by_id(job_id)
        if not job:
            raise AIJobNotFoundError(f"AI job {job_id} not found")

        if job.lease_owner != owner:
            raise ValueError(
                f"Job {job_id} is leased by another owner: {job.lease_owner}"
            )
        if job.status != AIJobStatus.PROCESSING:
            raise ValueError(
                f"Job {job_id} is not in PROCESSING state (status: {job.status})"
            )

        task_type = AITaskType(job.task_type)
        warnings: list[AISideEffectWarningCode] = []
        cache_identity = build_ai_cache_identity(
            task_type=task_type,
            request_context=request_context,
            prompt_version=job.prompt_version,
            schema_version=job.schema_version,
            language=language,
        )
        cache_policy = next(
            (item for item in self.config.cache_policies if item.task is task_type),
            None,
        )
        if cache_policy is not None and cache_policy.enabled:
            if self.cache_repository is None:
                raise ConfigurationError(
                    cause=ValueError("enabled AI cache requires a cache repository")
                )
            try:
                cached = await self.cache_repository.get(
                    cache_identity,
                    as_of=self.clock.utc_now(),
                )
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                cached = None
                warnings.append(AISideEffectWarningCode.CACHE_READ_FAILED)
                await self._append_audit(
                    event_type=AIAuditEventType.CACHE_SIDE_EFFECT_FAILED,
                    job_id=job.job_id,
                    post_id=job.post_id,
                    task_type=task_type,
                    prompt_version=job.prompt_version,
                    schema_version=job.schema_version,
                    warnings=warnings,
                    failure_category="cache_read_failed",
                )
            if cached is not None:
                age = max(
                    0.0,
                    (self.clock.utc_now() - cached.created_at).total_seconds(),
                )
                await self._append_audit(
                    event_type=AIAuditEventType.CACHE_HIT,
                    job_id=job.job_id,
                    post_id=job.post_id,
                    task_type=task_type,
                    prompt_version=job.prompt_version,
                    schema_version=job.schema_version,
                    warnings=warnings,
                    cache_hit=True,
                    success=True,
                )
                cache_result = cached.result.model_copy(
                    update={
                        "cache_hit": True,
                        "cache_age_seconds": age,
                        "side_effect_warnings": tuple(warnings),
                    }
                )
                completed_job = job.complete(
                    owner=owner,
                    result=cache_result.payload or {},
                    completed_at=self.clock.utc_now(),
                )
                await self.ai_job_repository.update(completed_job)
                return cache_result
            await self._append_audit(
                event_type=AIAuditEventType.CACHE_MISS,
                job_id=job.job_id,
                post_id=job.post_id,
                task_type=task_type,
                prompt_version=job.prompt_version,
                schema_version=job.schema_version,
                warnings=warnings,
                cache_hit=False,
            )

        # 2. Lookup failure policy
        failure_policy = None
        for fp in self.config.failure_policies:
            if fp.task == task_type:
                failure_policy = fp
                break
        if not failure_policy:
            raise ConfigurationError(
                cause=ValueError(f"No failure policy configured for task: {task_type}")
            )

        # 3. Select route candidates
        candidates = select_route_candidates(self.config, task_type)

        attempts_history: list[dict[str, Any]] = []
        retry_count = 0
        fallback_count = 0
        attempted_candidates_count = 0
        safe_last_failure_code = None
        temporary_unavailability: list[ProviderTemporarilyUnavailableError] = []

        # 4. Iterate over route candidates
        for fallback_idx, candidate in enumerate(candidates):
            provider_instance = self.providers_by_name.get(candidate.provider_name)
            if provider_instance is None:
                raise ConfigurationError(
                    cause=ValueError(
                        f"Instance for provider "
                        f"'{candidate.provider_name}' not provided"
                    )
                )
            provider: AIProvider = provider_instance

            if fallback_idx > 0:
                fallback_count += 1
                previous = candidates[fallback_idx - 1]
                fallback_type = (
                    AIAuditEventType.MODEL_FALLBACK
                    if previous.provider_name == candidate.provider_name
                    else AIAuditEventType.PROVIDER_FALLBACK
                )
                await self._append_audit(
                    event_type=fallback_type,
                    job_id=job.job_id,
                    post_id=job.post_id,
                    task_type=task_type,
                    prompt_version=job.prompt_version,
                    schema_version=job.schema_version,
                    warnings=warnings,
                    sequence_number=fallback_idx,
                    provider_name=candidate.provider_name,
                    model_name=candidate.model_name,
                    fallback_count=fallback_count,
                )

            current_attempt_in_candidate = 0

            async def run_attempt(
                provider: AIProvider = provider,
                candidate: AiRouteCandidateConfig = candidate,
                fallback_idx: int = fallback_idx,
                fallback_count_at_candidate: int = fallback_count,
            ) -> AIResult:
                nonlocal current_attempt_in_candidate, retry_count
                nonlocal attempted_candidates_count
                nonlocal safe_last_failure_code

                async def external_attempt() -> AIResult:
                    nonlocal current_attempt_in_candidate, retry_count
                    nonlocal attempted_candidates_count
                    nonlocal safe_last_failure_code
                    current_attempt_in_candidate += 1
                    if current_attempt_in_candidate == 1:
                        attempted_candidates_count += 1
                    else:
                        retry_count += 1
                        await self._append_audit(
                            event_type=AIAuditEventType.INTERNAL_RETRY,
                            job_id=job.job_id,
                            post_id=job.post_id,
                            task_type=task_type,
                            prompt_version=job.prompt_version,
                            schema_version=job.schema_version,
                            warnings=warnings,
                            sequence_number=len(attempts_history) + 1,
                            provider_name=candidate.provider_name,
                            model_name=candidate.model_name,
                            attempt_number=current_attempt_in_candidate,
                            retry_count=retry_count,
                            fallback_count=fallback_count_at_candidate,
                        )

                    start_time = self.clock.utc_now()
                    success = False
                    failure_category = None
                    prompt_tokens = None
                    completion_tokens = None
                    total_tokens = None
                    err_to_classify = None
                    http_status = None

                    try:
                        raw_envelope = await provider.execute_attempt(
                            task_type=task_type,
                            prompt=prompt_text,
                            request_context=request_context,
                            provider_name=candidate.provider_name,
                            model_name=candidate.model_name,
                            timeout_seconds=float(candidate.timeout_seconds),
                        )
                        prompt_tokens = raw_envelope.input_tokens
                        completion_tokens = raw_envelope.output_tokens
                        total_tokens = (
                            (prompt_tokens or 0) + (completion_tokens or 0)
                            if prompt_tokens is not None
                            or completion_tokens is not None
                            else None
                        )
                        http_status = raw_envelope.status_code
                        parsed_payload, _ = ResponseParser().parse(raw_envelope)
                        validated_output = ResponseValidator().validate(
                            parsed_payload, task_type, job.schema_version
                        )
                        ai_result = ResponseNormalizer().normalize(
                            envelope=raw_envelope,
                            validated_model=validated_output,
                            task_type=task_type,
                            provider_name=candidate.provider_name,
                            model_name=candidate.model_name,
                            prompt_version=job.prompt_version,
                            schema_version=job.schema_version,
                            attempt_number=current_attempt_in_candidate,
                            fallback_count=fallback_idx,
                        )
                        success = True
                        return ai_result
                    except Exception as err:
                        err_to_classify = err
                        from telegram_assist_bot.shared.errors import classify_error

                        failure_category = classify_error(err).category.value
                        safe_last_failure_code = failure_category
                        raise
                    finally:
                        from telegram_assist_bot.shared.errors import classify_error

                        duration = (self.clock.utc_now() - start_time).total_seconds()
                        attempts_history.append(
                            {
                                "sequence_number": len(attempts_history) + 1,
                                "provider_name": candidate.provider_name,
                                "model_name": candidate.model_name,
                                "internal_attempt_number": current_attempt_in_candidate,
                                "fallback_index": fallback_idx,
                                "duration_seconds": duration,
                                "success": success,
                                "failure_category": failure_category,
                                "retryable": (
                                    classify_error(err_to_classify).retryable
                                    if not success and err_to_classify
                                    else None
                                ),
                                "prompt_tokens": prompt_tokens,
                                "completion_tokens": completion_tokens,
                                "total_tokens": total_tokens,
                            }
                        )
                        event_type = (
                            AIAuditEventType.INVALID_PROVIDER_RESPONSE
                            if failure_category == "validation"
                            else AIAuditEventType.PROVIDER_ATTEMPT
                        )
                        await self._append_audit(
                            event_type=event_type,
                            job_id=job.job_id,
                            post_id=job.post_id,
                            task_type=task_type,
                            prompt_version=job.prompt_version,
                            schema_version=job.schema_version,
                            warnings=warnings,
                            sequence_number=len(attempts_history),
                            provider_name=candidate.provider_name,
                            model_name=candidate.model_name,
                            attempt_number=current_attempt_in_candidate,
                            retry_count=retry_count,
                            fallback_count=fallback_count_at_candidate,
                            success=success,
                            failure_category=failure_category,
                            http_status=http_status,
                            latency_seconds=duration,
                            input_tokens=prompt_tokens,
                            output_tokens=completion_tokens,
                        )
                        await self._increment_metrics(
                            provider_name=candidate.provider_name,
                            model_name=candidate.model_name,
                            delta=ProviderMetricDelta(
                                request_count=1,
                                success_count=int(success),
                                failure_count=int(not success),
                                timeout_count=int(failure_category == "timeout"),
                                rate_limit_count=int(failure_category == "rate_limit"),
                                invalid_response_count=int(
                                    failure_category == "validation"
                                ),
                                fallback_participation_count=int(fallback_idx > 0),
                                input_tokens=prompt_tokens or 0,
                                output_tokens=completion_tokens or 0,
                                total_tokens=total_tokens or 0,
                                cumulative_latency_seconds=max(0.0, duration),
                                latency_sample_count=1,
                                last_success_at=(
                                    self.clock.utc_now() if success else None
                                ),
                                last_error_at=(
                                    self.clock.utc_now() if not success else None
                                ),
                            ),
                            warnings=warnings,
                        )

                return await self.provider_guard.execute(
                    provider_name=candidate.provider_name,
                    model_name=candidate.model_name,
                    owner_id=owner,
                    policy=candidate.guard_policy,
                    operation=external_attempt,
                )

            try:
                # Bounded retry on the model
                ai_result = await execute_candidate_with_retry(
                    run_attempt,
                    max_attempts=candidate.max_attempts,
                    sleeper=self.sleeper,
                    jitter_source=self.jitter_source,
                )

                await self._append_audit(
                    event_type=AIAuditEventType.NORMALIZED_RESULT,
                    job_id=job.job_id,
                    post_id=job.post_id,
                    task_type=task_type,
                    prompt_version=job.prompt_version,
                    schema_version=job.schema_version,
                    warnings=warnings,
                    sequence_number=len(attempts_history) + 1,
                    provider_name=ai_result.provider_name,
                    model_name=ai_result.model_name,
                    success=True,
                )
                if cache_policy is not None and cache_policy.enabled:
                    ttl_seconds = cache_policy.ttl_seconds
                    cache_repository = self.cache_repository
                    if ttl_seconds is None or cache_repository is None:
                        raise ConfigurationError(
                            cause=ValueError("enabled AI cache is incomplete")
                        )
                    now = self.clock.utc_now()
                    entry = AICacheEntry(
                        identity=cache_identity,
                        result=ai_result,
                        created_at=now,
                        expires_at=now + timedelta(seconds=ttl_seconds),
                    )
                    try:
                        write_result = await cache_repository.put_if_absent(entry)
                        if not write_result.created:
                            ai_result = write_result.entry.result
                    except asyncio.CancelledError:
                        raise
                    except Exception:  # noqa: BLE001
                        warnings.append(AISideEffectWarningCode.CACHE_WRITE_FAILED)
                        await self._append_audit(
                            event_type=AIAuditEventType.CACHE_SIDE_EFFECT_FAILED,
                            job_id=job.job_id,
                            post_id=job.post_id,
                            task_type=task_type,
                            prompt_version=job.prompt_version,
                            schema_version=job.schema_version,
                            warnings=warnings,
                            failure_category="cache_write_failed",
                        )
                ai_result = ai_result.model_copy(
                    update={
                        "cache_hit": False,
                        "cache_age_seconds": None,
                        "side_effect_warnings": tuple(warnings),
                    }
                )

                # First valid result completes the job
                completed_job = job.complete(
                    owner=owner,
                    result=ai_result.payload or {},
                    completed_at=self.clock.utc_now(),
                )
                completed_job = replace(
                    completed_job,
                    attempts_history=attempts_history,
                    attempted_candidates_count=attempted_candidates_count,
                    retry_count=retry_count,
                    fallback_count=fallback_count,
                    safe_last_failure_code=None,
                )
                await self.ai_job_repository.update(completed_job)
                return ai_result

            except ProviderTemporarilyUnavailableError as error:
                temporary_unavailability.append(error)
                continue
            except Exception:  # noqa: BLE001, S112
                # Fallback to the next candidate
                continue

        # 5. If we reach here, all providers/candidates failed
        if attempted_candidates_count == 0 and temporary_unavailability:
            times = [
                error.next_eligible_at
                for error in temporary_unavailability
                if error.next_eligible_at is not None
            ]
            raise AllProvidersTemporarilyUnavailableError(min(times) if times else None)
        failed_job = job.fail(
            owner=owner,
            error=f"All providers failed. Last failure: {safe_last_failure_code}",
            next_run_delay_seconds=float(self.config.queue.next_run_delay_seconds),
            failed_at=self.clock.utc_now(),
        )
        failed_job = replace(
            failed_job,
            attempts_history=attempts_history,
            attempted_candidates_count=attempted_candidates_count,
            retry_count=retry_count,
            fallback_count=fallback_count,
            safe_last_failure_code=safe_last_failure_code,
        )
        await self.ai_job_repository.update(failed_job)
        await self._append_audit(
            event_type=AIAuditEventType.FINAL_FAILURE,
            job_id=job.job_id,
            post_id=job.post_id,
            task_type=task_type,
            prompt_version=job.prompt_version,
            schema_version=job.schema_version,
            warnings=warnings,
            sequence_number=len(attempts_history) + 1,
            success=False,
            failure_category=safe_last_failure_code,
            retry_count=retry_count,
            fallback_count=fallback_count,
        )

        raise AllProvidersFailedError(
            action=failure_policy.action,
            message=(
                f"All AI candidates failed for task {task_type}. "
                f"Action: {failure_policy.action}"
            ),
        )
