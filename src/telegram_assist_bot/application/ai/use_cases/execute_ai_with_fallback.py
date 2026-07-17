"""Use case for executing AI tasks with config-driven routing, retry, and fallback."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.response_normalizer import ResponseNormalizer
from telegram_assist_bot.application.ai.response_parser import ResponseParser
from telegram_assist_bot.application.ai.response_validator import ResponseValidator
from telegram_assist_bot.application.ai.retry import execute_candidate_with_retry
from telegram_assist_bot.application.ai.routing import select_route_candidates
from telegram_assist_bot.application.ports import AIJobNotFoundError
from telegram_assist_bot.domain.ai_job import AIJobStatus
from telegram_assist_bot.shared.errors import ConfigurationError

if TYPE_CHECKING:
    from pydantic import BaseModel

    from telegram_assist_bot.application.ai.contracts import AIResult
    from telegram_assist_bot.application.ai.retry import AsyncSleeper, JitterSource
    from telegram_assist_bot.application.ports import AIJobRepository, AIProvider
    from telegram_assist_bot.application.ports.clock import Clock
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
        providers_by_name: dict[str, AIProvider],
        ai_job_repository: AIJobRepository,
        clock: Clock,
        sleeper: AsyncSleeper,
        jitter_source: JitterSource,
    ) -> None:
        """Initialize the orchestrator."""
        self.config = config
        self.providers_by_name = providers_by_name
        self.ai_job_repository = ai_job_repository
        self.clock = clock
        self.sleeper = sleeper
        self.jitter_source = jitter_source

    async def execute(
        self,
        job_id: str,
        owner: str,
        prompt_text: str,
        request_context: BaseModel,
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

        # 4. Iterate over route candidates
        for fallback_idx, candidate in enumerate(candidates):
            provider = self.providers_by_name.get(candidate.provider_name)
            if not provider:
                raise ConfigurationError(
                    cause=ValueError(
                        f"Instance for provider "
                        f"'{candidate.provider_name}' not provided"
                    )
                )

            attempted_candidates_count += 1
            if fallback_idx > 0:
                fallback_count += 1

            current_attempt_in_candidate = 0

            async def run_attempt(
                provider: AIProvider = provider,
                candidate: AiRouteCandidateConfig = candidate,
                fallback_idx: int = fallback_idx,
            ) -> AIResult:
                nonlocal current_attempt_in_candidate, retry_count
                nonlocal safe_last_failure_code
                current_attempt_in_candidate += 1
                if current_attempt_in_candidate > 1:
                    retry_count += 1

                start_time = self.clock.utc_now()
                success = False
                failure_category = None
                prompt_tokens = None
                completion_tokens = None
                total_tokens = None
                err_to_classify = None

                try:
                    # Execute
                    raw_envelope = await provider.execute_attempt(
                        task_type=task_type,
                        prompt=prompt_text,
                        request_context=request_context,
                        provider_name=candidate.provider_name,
                        model_name=candidate.model_name,
                        timeout_seconds=float(candidate.timeout_seconds),
                    )

                    # Parse
                    parser = ResponseParser()
                    parsed_payload, _ = parser.parse(raw_envelope)

                    # Validate
                    validator = ResponseValidator()
                    validated_output = validator.validate(
                        parsed_payload, task_type, job.schema_version
                    )

                    # Normalize
                    normalizer = ResponseNormalizer()
                    ai_result = normalizer.normalize(
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

                    # Safe tokens
                    prompt_tokens = raw_envelope.input_tokens
                    completion_tokens = raw_envelope.output_tokens
                    total_tokens = (
                        (prompt_tokens or 0) + (completion_tokens or 0)
                        if prompt_tokens is not None
                        or completion_tokens is not None
                        else None
                    )

                    success = True
                    return ai_result

                except Exception as err:
                    err_to_classify = err
                    import traceback
                    traceback.print_exc()
                    from telegram_assist_bot.shared.errors import classify_error
                    classification = classify_error(err)
                    failure_category = classification.category.value
                    safe_last_failure_code = failure_category
                    raise

                finally:
                    end_time = self.clock.utc_now()
                    duration = (end_time - start_time).total_seconds()

                    from telegram_assist_bot.shared.errors import classify_error
                    # Build attempt telemetry record
                    attempt_record = {
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
                            if not success and err_to_classify else None
                        ),
                        "prompt_tokens": prompt_tokens,
                        "completion_tokens": completion_tokens,
                        "total_tokens": total_tokens,
                    }
                    attempts_history.append(attempt_record)

            try:
                # Bounded retry on the model
                ai_result = await execute_candidate_with_retry(
                    run_attempt,
                    max_attempts=candidate.max_attempts,
                    sleeper=self.sleeper,
                    jitter_source=self.jitter_source,
                )

                # First valid result completes the job
                completed_job = job.complete(
                    owner=owner,
                    result=ai_result.payload,
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

            except Exception:  # noqa: BLE001, S112
                # Fallback to the next candidate
                continue

        # 5. If we reach here, all providers/candidates failed
        failed_job = job.fail(
            owner=owner,
            error=f"All providers failed. Last failure: {safe_last_failure_code}",
            next_run_delay_seconds=float(
                self.config.queue.next_run_delay_seconds
            ),
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

        raise AllProvidersFailedError(
            action=failure_policy.action,
            message=(
                f"All AI candidates failed for task {task_type}. "
                f"Action: {failure_policy.action}"
            ),
        )
