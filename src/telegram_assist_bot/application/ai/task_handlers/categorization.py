"""Apply completed categorization AI Jobs to canonical Post processing state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Literal

from pydantic import ValidationError

from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.schemas import CategorizationOutput
from telegram_assist_bot.application.categorize_post import (
    KeywordCategoryRule,
    categorize_post,
)
from telegram_assist_bot.application.ports import (
    CategorizationPostUpdateRequest,
    PostConcurrencyConflictError,
)
from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditEventType,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.domain.categories import (
    CategorizationCheckFailure,
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
    Category,
)
from telegram_assist_bot.domain.posts import (
    InvalidPostIdentifierError,
    PostId,
    PostStatus,
)

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        AIJobRepository,
        CategorizationPostRepository,
        ContentPreparationRepository,
    )
    from telegram_assist_bot.application.ports.ai_audit_repository import (
        AIAuditRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.posts import Post
    from telegram_assist_bot.shared.config.models import ApplicationConfig


class CategorizationHandlerOutcome(StrEnum):
    """Describe one idempotent processing outcome without storage details."""

    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    RETRY_SCHEDULED = "retry_scheduled"
    STALE = "stale"
    CONFLICT = "conflict"


class CategorizationTaskValidationError(Exception):
    """Reject mismatched or incomplete persisted task data safely."""

    def __init__(self) -> None:
        """Avoid retaining raw payloads or validation details."""
        super().__init__("Persisted categorization task data is invalid.")


@dataclass(frozen=True, slots=True)
class CategorizationHandlingResult:
    """Return the canonical state and observable audit side-effect outcome."""

    outcome: CategorizationHandlerOutcome
    post: Post | None
    audit_persisted: bool | None


@dataclass(frozen=True, slots=True)
class CategorizationHandler:
    """Map one normalized task result or final failure through Post CAS."""

    posts: CategorizationPostRepository = field(repr=False)
    ai_jobs: AIJobRepository = field(repr=False)
    content_preparations: ContentPreparationRepository = field(repr=False)
    clock: Clock = field(repr=False)
    config: ApplicationConfig = field(repr=False)
    audit: AIAuditRepository | None = field(default=None, repr=False)

    async def complete(
        self,
        *,
        job_id: str,
        expected_job_version: int,
    ) -> CategorizationHandlingResult:
        """Apply one validated normalized result, including a valid cache hit."""
        job = await self._load_job(job_id, expected_job_version)
        if job.status is not AIJobStatus.COMPLETED or job.normalized_result is None:
            raise CategorizationTaskValidationError
        ai_result = self._normalized_result(job)

        post_id = self._post_id(job)
        post = await self.posts.get_by_id(post_id, as_of=self.clock.utc_now())
        if post is None:
            return CategorizationHandlingResult(
                CategorizationHandlerOutcome.STALE,
                None,
                None,
            )

        # Check if Post has moved to a terminal or later incompatible stage.
        if post.status is PostStatus.EXPIRED or post.categorization_state in {
            CategorizationState.SUPERSEDED_MANUAL,
        }:
            return CategorizationHandlingResult(
                CategorizationHandlerOutcome.STALE,
                post,
                None,
            )

        # Check manual override concurrency
        current_res = await self.content_preparations.get_category_result(post_id)
        if (
            current_res is not None
            and current_res.method is CategorizationMethod.MANUAL
        ):
            # Complete AIJob idempotently, preserve manual override
            await self._append_audit(
                job,
                AIAuditEventType.CATEGORIZATION_RESULT_SUPERSEDED,
                success=True,
                failure_category=None,
                provider_name=ai_result.provider_name,
                model_name=ai_result.model_name,
                cache_hit=ai_result.cache_hit,
            )
            return CategorizationHandlingResult(
                CategorizationHandlerOutcome.IDEMPOTENT,
                post,
                True,
            )

        # Parse output and validate confidence boundaries
        try:
            output = CategorizationOutput.model_validate(ai_result.result)
        except ValidationError:
            raise CategorizationTaskValidationError from None

        if not 0.0 <= output.confidence <= 1.0:
            raise CategorizationTaskValidationError

        # Map to current active Taxonomy and resolve aliases.
        categories = tuple(
            Category(c.category_id, c.display_name, c.active)
            for c in self.config.categorization.categories
        )
        active_ids = {c.category_id for c in categories if c.active}
        aliases = self.config.categorization.aliases or {}

        resolved_category_id = None
        output_cat = output.category_id
        if output_cat in active_ids:
            resolved_category_id = output_cat
        elif output_cat in aliases:
            target_id = aliases[output_cat]
            if target_id in active_ids:
                resolved_category_id = target_id

        # If invalid category/alias target -> trigger baseline fallback
        if resolved_category_id is None:
            return await self._apply_baseline_fallback(post, job, ai_result)

        # Success: build CategorizationResult
        result = CategorizationResult(
            category_id=resolved_category_id,
            method=CategorizationMethod.AI,
            policy_version=2,
            assigned_at=self.clock.utc_now(),
            reason=output.reason,
            confidence=output.confidence,
            provider_name=ai_result.provider_name,
            model_name=ai_result.model_name,
            prompt_version=ai_result.prompt_version,
            schema_version=ai_result.schema_version,
            cache_hit=ai_result.cache_hit,
            cache_age=ai_result.cache_age_seconds,
        )

        previous_state = post.categorization_state
        previous_version = post.categorization_processing_version
        target = post.apply_categorization_result(
            result,
            job_id=job.job_id,
            expected_processing_version=previous_version,
        )

        persisted, outcome = await self._persist_or_resolve(
            target,
            previous_state,
            previous_version,
        )
        if outcome is not CategorizationHandlerOutcome.APPLIED:
            return CategorizationHandlingResult(outcome, persisted, None)

        # Save result to preparations collection
        await self.content_preparations.save_category_result(post_id, result)

        audit_persisted = await self._append_audit(
            job,
            AIAuditEventType.CATEGORIZATION_RESULT_APPLIED,
            success=True,
            failure_category=None,
            provider_name=result.provider_name,
            model_name=result.model_name,
            cache_hit=bool(result.cache_hit),
        )
        return CategorizationHandlingResult(outcome, persisted, audit_persisted)

    async def fail(
        self,
        *,
        job_id: str,
        expected_job_version: int,
        policy: Literal["fallback_baseline", "retry_later"],
    ) -> CategorizationHandlingResult:
        """Apply exactly one approved final-failure or future-retry policy."""
        job = await self._load_job(job_id, expected_job_version)
        retry_later = policy == "retry_later"
        required_status = (
            AIJobStatus.WAITING_FOR_RETRY
            if retry_later
            else AIJobStatus.ALL_PROVIDERS_FAILED
        )
        if job.status is not required_status:
            raise CategorizationTaskValidationError

        post_id = self._post_id(job)
        post = await self.posts.get_by_id(post_id, as_of=self.clock.utc_now())
        if post is None:
            return CategorizationHandlingResult(
                CategorizationHandlerOutcome.STALE,
                None,
                None,
            )

        if post.categorization_state in {
            CategorizationState.SUPERSEDED_MANUAL,
        }:
            return CategorizationHandlingResult(
                CategorizationHandlerOutcome.STALE,
                post,
                None,
            )

        # Check manual override concurrency
        current_res = await self.content_preparations.get_category_result(post_id)
        if (
            current_res is not None
            and current_res.method is CategorizationMethod.MANUAL
        ):
            return CategorizationHandlingResult(
                CategorizationHandlerOutcome.IDEMPOTENT,
                post,
                None,
            )

        if not retry_later:
            # If all providers failed -> trigger baseline fallback
            return await self._apply_baseline_fallback(post, job, None)

        # Retry later -> transition post state to RETRY_PENDING
        failure = CategorizationCheckFailure(
            policy="retry_later",
            failure_category=job.safe_last_failure_code or "unknown",
            failed_at=job.updated_at or self.clock.utc_now(),
            attempted_candidates_count=job.attempted_candidates_count or 0,
            retry_count=job.retry_count or 0,
            fallback_count=job.fallback_count or 0,
            next_retry_at=job.next_run_at,
        )

        previous_state = post.categorization_state
        previous_version = post.categorization_processing_version
        target = post.apply_categorization_failure(
            failure,
            job_id=job.job_id,
            expected_processing_version=previous_version,
        )

        persisted, outcome = await self._persist_or_resolve(
            target,
            previous_state,
            previous_version,
        )
        if outcome is not CategorizationHandlerOutcome.APPLIED:
            return CategorizationHandlingResult(outcome, persisted, None)

        audit_persisted = await self._append_audit(
            job,
            AIAuditEventType.CATEGORIZATION_FAILURE_POLICY_APPLIED,
            success=False,
            failure_category=failure.failure_category,
            provider_name=None,
            model_name=None,
            cache_hit=False,
        )
        return CategorizationHandlingResult(
            CategorizationHandlerOutcome.RETRY_SCHEDULED,
            persisted,
            audit_persisted,
        )

    async def _apply_baseline_fallback(
        self,
        post: Post,
        job: AIJob,
        ai_result: AIResult | None,
    ) -> CategorizationHandlingResult:
        """Run the baseline categorization algorithms and apply fallback."""
        post_id = post.post_id
        method_order = self.config.categorization.method_order
        if method_order is None or "ai" not in method_order:
            raise CategorizationTaskValidationError
        trailing_methods = method_order[method_order.index("ai") + 1 :]

        # Load source channel default category ID
        source_config = next(
            (
                sc
                for sc in self.config.source_channels
                if sc.telegram_channel_id == post.source_identity.source_channel_id
            ),
            None,
        )
        if not source_config:
            raise CategorizationTaskValidationError

        source_default_category_id = source_config.default_category_id

        # Build category keyword rules
        categories = tuple(
            Category(c.category_id, c.display_name, c.active)
            for c in self.config.categorization.categories
        )
        rules = tuple(
            KeywordCategoryRule(r.rule_id, r.category_id, r.keyword, r.priority)
            for r in self.config.categorization.keyword_rules
        )

        text_input = post.original_content.text or post.original_content.caption or ""
        result = self._trailing_baseline_result(
            methods=trailing_methods,
            text=text_input,
            categories=categories,
            rules=rules,
            source_default_category_id=source_default_category_id or "",
        )
        if result is None:
            raise CategorizationTaskValidationError

        previous_state = post.categorization_state
        previous_version = post.categorization_processing_version
        target = post.apply_categorization_result(
            result,
            job_id=job.job_id,
            expected_processing_version=previous_version,
        )

        persisted, outcome = await self._persist_or_resolve(
            target,
            previous_state,
            previous_version,
        )
        if outcome is not CategorizationHandlerOutcome.APPLIED:
            return CategorizationHandlingResult(outcome, persisted, None)

        # Save result to preparations collection
        await self.content_preparations.save_category_result(post_id, result)

        # Audit fallback application
        audit_persisted = await self._append_audit(
            job,
            AIAuditEventType.CATEGORIZATION_FAILURE_POLICY_APPLIED,
            success=False,
            failure_category=job.safe_last_failure_code or "invalid_category",
            provider_name=ai_result.provider_name if ai_result else None,
            model_name=ai_result.model_name if ai_result else None,
            cache_hit=ai_result.cache_hit if ai_result else False,
        )
        return CategorizationHandlingResult(outcome, persisted, audit_persisted)

    async def _persist_or_resolve(
        self,
        target: Post,
        previous_state: CategorizationState,
        previous_version: int,
    ) -> tuple[Post, CategorizationHandlerOutcome]:
        try:
            persisted = await self.posts.update_categorization(
                CategorizationPostUpdateRequest(
                    target,
                    expected_processing_version=previous_version,
                    expected_processing_state=previous_state,
                )
            )
            return persisted, CategorizationHandlerOutcome.APPLIED
        except PostConcurrencyConflictError:
            current = await self.posts.get_by_id(
                target.post_id,
                as_of=self.clock.utc_now(),
            )
            if current is None:
                return target, CategorizationHandlerOutcome.CONFLICT
            if (
                current.categorization_result is not None
                and current.categorization_result.method is CategorizationMethod.MANUAL
            ):
                return current, CategorizationHandlerOutcome.IDEMPOTENT
            if (
                current.categorization_processing_version > previous_version
                and current.categorization_state is target.categorization_state
                and current.categorization_job_id == target.categorization_job_id
            ):
                return current, CategorizationHandlerOutcome.IDEMPOTENT
            return current, CategorizationHandlerOutcome.CONFLICT

    async def _load_job(self, job_id: str, expected_version: int) -> AIJob:
        if (
            type(job_id) is not str
            or not job_id
            or type(expected_version) is not int
            or expected_version < 0
        ):
            raise CategorizationTaskValidationError
        job = await self.ai_jobs.get_by_id(job_id)
        if (
            job is None
            or job.version != expected_version
            or job.task_type != AITaskType.CATEGORIZATION.value
            or job.prompt_version != "2.0.0"
            or job.schema_version != "2"
        ):
            raise CategorizationTaskValidationError
        return job

    @staticmethod
    def _post_id(job: AIJob) -> PostId:
        try:
            return PostId(job.post_id)
        except InvalidPostIdentifierError:
            raise CategorizationTaskValidationError from None

    @staticmethod
    def _normalized_result(job: AIJob) -> AIResult:
        try:
            result = AIResult.model_validate(job.normalized_result)
        except ValidationError:
            raise CategorizationTaskValidationError from None
        if (
            not result.success
            or result.task_type is not AITaskType.CATEGORIZATION
            or result.prompt_version != job.prompt_version
            or result.schema_version != job.schema_version
        ):
            raise CategorizationTaskValidationError
        return result

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
    ) -> bool:
        if self.audit is None:
            return False
        event = AIAuditEvent(
            event_id=hashlib.sha256(
                f"{job.job_id}|{event_type.value}".encode("utf-8")  # noqa: UP012
            ).hexdigest(),
            task_type=AITaskType.CATEGORIZATION,
            event_type=event_type,
            post_id=job.post_id,
            job_id=job.job_id,
            success=success,
            failure_category=failure_category,
            provider_name=provider_name,
            model_name=model_name,
            prompt_version=job.prompt_version,
            schema_version=job.schema_version,
            cache_hit=cache_hit,
            occurred_at=self.clock.utc_now(),
        )
        return await self.audit.append(event)

    def _trailing_baseline_result(
        self,
        *,
        methods: tuple[str, ...],
        text: str,
        categories: tuple[Category, ...],
        rules: tuple[KeywordCategoryRule, ...],
        source_default_category_id: str,
    ) -> CategorizationResult | None:
        for method in methods:
            if method == "keyword":
                result = categorize_post(
                    text=text,
                    categories=categories,
                    rules=rules,
                    source_default_category_id=source_default_category_id,
                    assigned_at=self.clock.utc_now(),
                )
                if result.method is CategorizationMethod.KEYWORD:
                    return result
            elif method == "source_default":
                return CategorizationResult(
                    category_id=source_default_category_id,
                    method=CategorizationMethod.SOURCE_DEFAULT,
                    policy_version=1,
                    assigned_at=self.clock.utc_now(),
                    reason="source_default",
                )
        return None
