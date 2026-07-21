"""AI worker polling loop for claiming and executing AI jobs."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from telegram_assist_bot.application.ai.contracts import AITaskType
from telegram_assist_bot.application.ai.provider_guard import (
    AllProvidersTemporarilyUnavailableError,
)
from telegram_assist_bot.application.ai.schemas import (
    AdvertisementDetectionContext,
    CategorizationContext,
    SemanticDuplicateContext,
)
from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
    AllProvidersFailedError,
)
from telegram_assist_bot.application.text_normalization import normalize_exact_text
from telegram_assist_bot.domain.ai_job import AIJob
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    bind_log_context,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from telegram_assist_bot.application.ai.prompt_registry import PromptRegistry
    from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
        ExecuteAIWithFallback,
    )
    from telegram_assist_bot.application.ports import (
        AIJobRepository,
        PostRepository,
        SemanticDuplicateCandidateRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.shared.config.models import ApplicationConfig
    from telegram_assist_bot.shared.observability import StructuredLogger


class AIWorker:
    """Supervised worker that claims and processes queued AI tasks."""

    def __init__(
        self,
        owner: str,
        ai_job_repository: AIJobRepository,
        post_repository: PostRepository,
        execute_ai_with_fallback: ExecuteAIWithFallback,
        prompt_registry: PromptRegistry,
        clock: Clock,
        config: ApplicationConfig,
        task_handlers: Mapping[AITaskType, Any],
        semantic_candidates: SemanticDuplicateCandidateRepository | None = None,
        poll_seconds: float = 5.0,
        logger: StructuredLogger | None = None,
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        """Initialize the AI worker with required dependencies."""
        self.owner = owner
        self.ai_job_repository = ai_job_repository
        self.post_repository = post_repository
        self.execute_ai_with_fallback = execute_ai_with_fallback
        self.prompt_registry = prompt_registry
        self.clock = clock
        self.config = config
        self.task_handlers = task_handlers
        self.semantic_candidates = semantic_candidates
        self.poll_seconds = poll_seconds
        self.logger = logger
        self.sleeper = sleeper

    async def run(self) -> None:
        """Run the polling loop until cancelled."""
        if self.logger:
            self.logger.emit(
                level=LogLevel.INFO,
                event_name="ai_worker_started",
                fields={
                    "owner": self.owner,
                    "poll_seconds": self.poll_seconds,
                },
            )
        while True:
            try:
                processed = await self.execute_once()
                if not processed:
                    await self.sleeper(self.poll_seconds)
            except asyncio.CancelledError:
                if self.logger:
                    self.logger.emit(
                        level=LogLevel.INFO,
                        event_name="ai_worker_cancelled",
                        fields={"owner": self.owner},
                    )
                raise
            except Exception as err:  # noqa: BLE001
                if self.logger:
                    self.logger.emit(
                        level=LogLevel.ERROR,
                        event_name="ai_worker_iteration_failed",
                        fields={"owner": self.owner},
                        error=err,
                    )
                await self.sleeper(self.poll_seconds)

    async def execute_once(self) -> bool:
        """Claim and execute at most one due AI job.

        Returns True if a job was claimed, False otherwise.
        """
        now = self.clock.utc_now()
        job = await self.ai_job_repository.claim_next_due(
            owner=self.owner,
            lease_duration_seconds=float(self.config.ai.queue.lease_duration_seconds),
            as_of=now,
        )
        if job is None:
            return False

        correlation_id = f"ai-job-{job.job_id}"
        context = CorrelationContext(
            correlation_id=correlation_id,
            job_id=job.job_id,
            post_id=job.post_id,
        )

        with bind_log_context(context):
            if self.logger:
                self.logger.emit(
                    level=LogLevel.INFO,
                    event_name="ai_job_claimed",
                    fields={
                        "owner": self.owner,
                        "attempts": job.attempts,
                        "version": job.version,
                    },
                )
            try:
                await self._process_job(job)
            except asyncio.CancelledError:
                if self.logger:
                    self.logger.emit(
                        level=LogLevel.WARNING,
                        event_name="ai_job_execution_cancelled",
                        fields={"owner": self.owner},
                    )
                with suppress(Exception):
                    released = job.release(self.owner, self.clock.utc_now())
                    await self.ai_job_repository.update(released)
                raise
            except AllProvidersTemporarilyUnavailableError as err:
                if self.logger:
                    self.logger.emit(
                        level=LogLevel.WARNING,
                        event_name="ai_providers_temporarily_unavailable",
                        fields={
                            "owner": self.owner,
                            "next_eligible_at": (
                                err.next_eligible_at.isoformat()
                                if err.next_eligible_at
                                else None
                            ),
                        },
                    )
                with suppress(Exception):
                    released = job.release(self.owner, self.clock.utc_now())
                    if err.next_eligible_at:
                        import dataclasses

                        released = dataclasses.replace(
                            released,
                            next_run_at=err.next_eligible_at,
                        )
                    await self.ai_job_repository.update(released)
            except Exception as err:  # noqa: BLE001
                if self.logger:
                    self.logger.emit(
                        level=LogLevel.ERROR,
                        event_name="ai_job_processing_failed",
                        fields={"owner": self.owner},
                        error=err,
                    )
                with suppress(Exception):
                    released = job.release(self.owner, self.clock.utc_now())
                    await self.ai_job_repository.update(released)
            return True

    async def _process_job(self, job: AIJob) -> None:
        task_type = AITaskType(job.task_type)
        post = await self.post_repository.get_by_id(
            PostId(job.post_id), as_of=self.clock.utc_now()
        )
        if post is None:
            expired = job.expire(self.clock.utc_now())
            await self.ai_job_repository.update(expired)
            if self.logger:
                self.logger.emit(
                    level=LogLevel.WARNING,
                    event_name="ai_job_post_missing",
                    fields={"owner": self.owner},
                )
            return

        handler = self.task_handlers.get(task_type)
        if handler is None:
            if self.logger:
                self.logger.emit(
                    level=LogLevel.ERROR,
                    event_name="ai_job_unknown_task_type",
                    fields={"task_type": job.task_type},
                )
            released = job.release(self.owner, self.clock.utc_now())
            await self.ai_job_repository.update(released)
            return

        if task_type is AITaskType.ADVERTISEMENT_DETECTION:
            text = post.original_content.text or post.original_content.caption
            if not text:
                await self.ai_job_repository.update(job.expire(self.clock.utc_now()))
                return
            prompt = self.prompt_registry.get_prompt(
                AITaskType.ADVERTISEMENT_DETECTION, job.prompt_version
            )
            prompt_text = prompt.body.format(text=text)
            ad_context = AdvertisementDetectionContext(text=text)

            try:
                await self.execute_ai_with_fallback.execute(
                    job_id=job.job_id,
                    owner=self.owner,
                    prompt_text=prompt_text,
                    request_context=ad_context,
                    language="fa",
                )
                await handler.complete(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                )
            except AllProvidersFailedError:
                await handler.fail(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                )

        elif task_type is AITaskType.SEMANTIC_DUPLICATE:
            source = post.original_content.text or post.original_content.caption
            normalized = normalize_exact_text(source)
            if not normalized:
                await self.ai_job_repository.update(job.expire(self.clock.utc_now()))
                return

            if self.semantic_candidates is None:
                raise ValueError("Semantic duplicate candidate repository is required")
            candidates = await self.semantic_candidates.list_candidates(
                current_post_id=post.post_id,
                now=self.clock.utc_now(),
                window_start=self.clock.utc_now() - timedelta(days=14),
                limit=100,
            )
            if self.config.semantic_duplicate is None:
                raise ValueError("Semantic duplicate configuration is missing")

            if not candidates:
                import dataclasses

                completed_job = job.complete(self.owner, {}, self.clock.utc_now())
                completed_job = dataclasses.replace(
                    completed_job,
                    semantic_candidate_results=[],
                )
                await self.ai_job_repository.update(completed_job)
                await handler.complete(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                    threshold=self.config.semantic_duplicate.threshold,
                    duplicate_policy=self.config.semantic_duplicate.duplicate_policy,
                )
                return

            prompt = self.prompt_registry.get_prompt(
                AITaskType.SEMANTIC_DUPLICATE, job.prompt_version
            )

            candidate_results = []
            best_result = None
            best_similarity = -1.0
            all_failed = False
            last_err = None

            for cand in candidates:
                temp_job_id = f"temp_{job.job_id}_{cand.post_id.value}"
                temp_post_id = f"{job.post_id}_{cand.post_id.value}"
                temp_job = AIJob.create(
                    job_id=temp_job_id,
                    post_id=temp_post_id,
                    task_type=job.task_type,
                    prompt_version=job.prompt_version,
                    schema_version=job.schema_version,
                    priority=job.priority,
                    max_attempts=1,
                )
                temp_job = temp_job.claim(
                    owner=self.owner,
                    lease_duration_seconds=300,
                    claimed_at=self.clock.utc_now(),
                )
                await self.ai_job_repository.enqueue(temp_job)

                try:
                    res = await self.execute_ai_with_fallback.execute(
                        job_id=temp_job_id,
                        owner=self.owner,
                        prompt_text=prompt.body.format(
                            text=normalized, compare_text=cand.comparison_text
                        ),
                        request_context=SemanticDuplicateContext(
                            text=normalized,
                            compare_text=cand.comparison_text,
                            similarity_threshold=self.config.semantic_duplicate.threshold,
                        ),
                        language="fa",
                    )
                    candidate_results.append(
                        {
                            "candidate_post_id": cand.post_id.value,
                            "result": res.model_dump(mode="json"),
                        }
                    )
                    from telegram_assist_bot.application.ai.schemas import (
                        SemanticDuplicateOutput,
                    )

                    output = SemanticDuplicateOutput.model_validate(res.result)
                    if output.similarity > best_similarity:
                        best_similarity = output.similarity
                        best_result = res
                except AllProvidersFailedError as err:
                    all_failed = True
                    last_err = err
                    break
                finally:
                    from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (  # noqa: E501
                        MongoAIJobRepository,
                    )

                    if isinstance(self.ai_job_repository, MongoAIJobRepository):
                        await self.ai_job_repository._collection.delete_one(
                            {"_id": temp_job_id}
                        )

            if all_failed:
                failed_job = job.fail(
                    owner=self.owner,
                    error=str(last_err),
                    next_run_delay_seconds=float(
                        self.config.ai.queue.next_run_delay_seconds
                    ),
                    failed_at=self.clock.utc_now(),
                )
                await self.ai_job_repository.update(failed_job)
                fp_config = next(
                    (
                        fp
                        for fp in self.config.ai.failure_policies
                        if fp.task == AITaskType.SEMANTIC_DUPLICATE
                    ),
                    None,
                )
                from telegram_assist_bot.domain.duplicates import (
                    SemanticDuplicateFailurePolicy,
                )

                policy = (
                    SemanticDuplicateFailurePolicy(fp_config.action.value)
                    if fp_config
                    else SemanticDuplicateFailurePolicy.MANUAL_REVIEW
                )
                await handler.fail(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                    policy=policy,
                )
            else:
                import dataclasses

                if best_result is None:
                    raise ValueError(
                        "Best result is missing for semantic duplicate checks"
                    )
                completed_job = job.complete(
                    self.owner, best_result.payload or {}, self.clock.utc_now()
                )
                completed_job = dataclasses.replace(
                    completed_job,
                    normalized_result=best_result.model_dump(mode="json"),
                    semantic_candidate_results=candidate_results,
                )
                await self.ai_job_repository.update(completed_job)
                await handler.complete(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                    threshold=self.config.semantic_duplicate.threshold,
                    duplicate_policy=self.config.semantic_duplicate.duplicate_policy,
                )

        elif task_type is AITaskType.CATEGORIZATION:
            text = post.original_content.text or post.original_content.caption
            if not text:
                await self.ai_job_repository.update(job.expire(self.clock.utc_now()))
                return
            prompt = self.prompt_registry.get_prompt(
                AITaskType.CATEGORIZATION, job.prompt_version
            )
            allowed_categories = [
                item.category_id
                for item in self.config.categorization.categories
                if item.active
            ]
            prompt_text = prompt.body.format(
                allowed_categories=", ".join(allowed_categories), text=text
            )
            cat_context = CategorizationContext(
                text=text, allowed_categories=allowed_categories
            )

            try:
                await self.execute_ai_with_fallback.execute(
                    job_id=job.job_id,
                    owner=self.owner,
                    prompt_text=prompt_text,
                    request_context=cat_context,
                    language="fa",
                )
                await handler.complete(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                )
            except AllProvidersFailedError:
                await handler.fail(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                )

        elif task_type is AITaskType.SCORING:
            score_context = await handler.prepare_claimed(
                job_id=job.job_id,
                expected_job_version=job.version,
                lease_owner=self.owner,
            )
            if score_context is None:
                return

            prompt = self.prompt_registry.get_prompt(
                AITaskType.SCORING, job.prompt_version
            )
            prompt_text = prompt.body.format(text=score_context.text)

            try:
                await self.execute_ai_with_fallback.execute(
                    job_id=job.job_id,
                    owner=self.owner,
                    prompt_text=prompt_text,
                    request_context=score_context,
                    language="fa",
                )
                await handler.complete(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                )
            except AllProvidersFailedError:
                await handler.fail(
                    job_id=job.job_id,
                    expected_job_version=job.version + 1,
                )
