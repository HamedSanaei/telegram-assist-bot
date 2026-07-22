"""Coordinate isolated AI categorization with the T018 baseline methods."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from pydantic import BaseModel, ValidationError

from telegram_assist_bot.application.ai.cache_key import build_ai_cache_identity
from telegram_assist_bot.application.ai.schemas import CategorizationOutput
from telegram_assist_bot.application.ports import CategorizationPostUpdateRequest
from telegram_assist_bot.domain.advertisement import AdvertisementProcessingState
from telegram_assist_bot.domain.ai_task import AITaskType
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
)
from telegram_assist_bot.domain.duplicates import SemanticDuplicateState
from telegram_assist_bot.domain.posts import PostStatus

if TYPE_CHECKING:
    from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
    from telegram_assist_bot.application.ports import (
        CategorizationPostRepository,
        ContentPreparationRepository,
    )
    from telegram_assist_bot.application.ports.ai_cache_repository import (
        AICacheRepository,
    )
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.application.prepare_post_pipeline import PreparationInput
    from telegram_assist_bot.domain.posts import Post
    from telegram_assist_bot.shared.config.models import ApplicationConfig

_PROMPT_VERSION = "2.0.0"
_SCHEMA_VERSION = "2"
_LANGUAGE = "fa"


class CategorizationRequestContext(BaseModel):
    """Hold only approved content and taxonomy identity for cache hashing."""

    text: str
    taxonomy_fingerprint: str


@dataclass(frozen=True, slots=True)
class CategorizeWithAI:
    """Select manual, configured baseline, cached AI, or durable AI work."""

    config: ApplicationConfig = field(repr=False)
    content_repo: ContentPreparationRepository = field(repr=False)
    post_repo: CategorizationPostRepository = field(repr=False)
    enqueue_job: EnqueueAIJob = field(repr=False)
    clock: Clock = field(repr=False)
    cache_repo: AICacheRepository | None = field(default=None, repr=False)

    async def execute(
        self,
        request: PreparationInput,
        post: Post,
    ) -> CategorizationResult | None:
        """Apply the configured precedence without contacting an AI provider."""
        manual = await self._manual_result(request, post)
        if manual is not None:
            return manual

        if not self.config.features.ai_categorization_enabled:
            return self._baseline_result(request)

        method_order = self.config.categorization.method_order
        if (
            method_order is None
            or self.config.categorization.fallback_policy != "fallback_baseline"
        ):
            raise ValueError("Explicit AI categorization policy is required.")

        for method in method_order:
            if method == "keyword":
                result = self._keyword_result(request)
                if result is not None:
                    return result
            elif method == "ai":
                return await self._cached_or_enqueued(request, post)
            elif method == "source_default":
                return self._source_default_result(request)
        raise ValueError("Categorization method order is invalid.")

    async def _manual_result(
        self,
        request: PreparationInput,
        post: Post,
    ) -> CategorizationResult | None:
        result = request.manual_category
        if result is None:
            result = await self.content_repo.get_category_result(post.post_id)
        if result is None or result.method is not CategorizationMethod.MANUAL:
            return None
        active_ids = {item.category_id for item in request.categories if item.active}
        if result.category_id not in active_ids:
            raise ValueError("Manual category override is invalid.")
        return result

    async def _cached_or_enqueued(
        self,
        request: PreparationInput,
        post: Post,
    ) -> CategorizationResult | None:
        if not self._prerequisites_allow_ai(post):
            return None

        identity = build_ai_cache_identity(
            task_type=AITaskType.CATEGORIZATION,
            request_context=CategorizationRequestContext(
                text=request.text or request.caption or "",
                taxonomy_fingerprint=self._taxonomy_fingerprint(request),
            ),
            prompt_version=_PROMPT_VERSION,
            schema_version=_SCHEMA_VERSION,
            language=_LANGUAGE,
        )
        if self.cache_repo is not None:
            cached = await self.cache_repo.get(identity, as_of=self.clock.utc_now())
            if cached is not None:
                result = self._result_from_cache(request, cached.result)
                if result is not None:
                    await self._persist_cached_result(post, result)
                    return result

        job_id = self._job_id(post.post_id.value)
        if (
            post.categorization_state is CategorizationState.PENDING
            and post.categorization_job_id == job_id
        ):
            return None
        enqueue_result = await self.enqueue_job.execute(
            post_id=post.post_id.value,
            task_type=AITaskType.CATEGORIZATION.value,
            prompt_version=_PROMPT_VERSION,
            schema_version=_SCHEMA_VERSION,
            priority=20,
            job_id=job_id,
        )
        target = post.enqueue_categorization(enqueue_result.job.job_id)
        await self.post_repo.update_categorization(
            CategorizationPostUpdateRequest(
                target,
                expected_processing_version=post.categorization_processing_version,
                expected_processing_state=post.categorization_state,
            )
        )
        return None

    async def _persist_cached_result(
        self,
        post: Post,
        result: CategorizationResult,
    ) -> None:
        target = post.apply_categorization_result(
            result,
            job_id=None,
            expected_processing_version=post.categorization_processing_version,
        )
        await self.post_repo.update_categorization(
            CategorizationPostUpdateRequest(
                target,
                expected_processing_version=post.categorization_processing_version,
                expected_processing_state=post.categorization_state,
            )
        )
        await self.content_repo.save_category_result(post.post_id, result)

    def _result_from_cache(
        self,
        request: PreparationInput,
        cached: object,
    ) -> CategorizationResult | None:
        from telegram_assist_bot.application.ai.contracts import AIResult

        if not isinstance(cached, AIResult):
            return None
        if (
            not cached.success
            or cached.task_type is not AITaskType.CATEGORIZATION
            or cached.prompt_version != _PROMPT_VERSION
            or cached.schema_version != _SCHEMA_VERSION
        ):
            return None
        try:
            output = CategorizationOutput.model_validate(cached.result)
        except ValidationError:
            return None
        category_id = self._resolve_category_id(request, output.category_id)
        if category_id is None:
            return None
        return CategorizationResult(
            category_id=category_id,
            method=CategorizationMethod.AI,
            policy_version=2,
            assigned_at=self.clock.utc_now(),
            reason=output.reason,
            confidence=output.confidence,
            provider_name=cached.provider_name,
            model_name=cached.model_name,
            prompt_version=cached.prompt_version,
            schema_version=cached.schema_version,
            cache_hit=True,
            cache_age=cached.cache_age_seconds,
            attempt_number=cached.attempt_number,
            fallback_count=cached.fallback_count,
        )

    def _resolve_category_id(
        self,
        request: PreparationInput,
        value: str,
    ) -> str | None:
        active_ids = {item.category_id for item in request.categories if item.active}
        if value in active_ids:
            return value
        aliases = self.config.categorization.aliases or {}
        target = aliases.get(value)
        return target if target in active_ids else None

    def _baseline_result(self, request: PreparationInput) -> CategorizationResult:
        return self._keyword_result(request) or self._source_default_result(request)

    def _keyword_result(
        self,
        request: PreparationInput,
    ) -> CategorizationResult | None:
        text = request.text or request.caption or ""
        active_ids = {item.category_id for item in request.categories if item.active}
        matches = []
        for rule in request.category_rules:
            if rule.category_id not in active_ids:
                continue
            pattern = re.compile(
                rf"(?<![\w‌]){re.escape(rule.keyword)}(?![\w‌])",
                re.IGNORECASE,
            )
            if pattern.search(text):
                matches.append(rule)
        if not matches:
            return None
        winner = min(matches, key=lambda item: (-item.priority, item.rule_id))
        return CategorizationResult(
            category_id=winner.category_id,
            method=CategorizationMethod.KEYWORD,
            policy_version=1,
            assigned_at=self.clock.utc_now(),
            rule_id=winner.rule_id,
            reason="keyword_rule",
        )

    def _source_default_result(
        self,
        request: PreparationInput,
    ) -> CategorizationResult:
        return CategorizationResult(
            category_id=request.source_default_category_id,
            method=CategorizationMethod.SOURCE_DEFAULT,
            policy_version=1,
            assigned_at=self.clock.utc_now(),
            reason="source_default",
        )

    def _taxonomy_fingerprint(self, request: PreparationInput) -> str:
        payload = {
            "active_category_ids": sorted(
                item.category_id for item in request.categories if item.active
            ),
            "aliases": dict(sorted((self.config.categorization.aliases or {}).items())),
        }
        canonical = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _prerequisites_allow_ai(self, post: Post) -> bool:
        advertisement_ready = (
            not self.config.features.advertisement_detection_enabled
            and post.advertisement_state is AdvertisementProcessingState.NOT_REQUESTED
        ) or post.advertisement_state in {
            AdvertisementProcessingState.PASSED,
            AdvertisementProcessingState.FAILED_CONTINUE,
        }
        duplicate_ready = (
            not self.config.features.duplicate_detection_enabled
            and post.semantic_duplicate_state is SemanticDuplicateState.NOT_REQUESTED
        ) or post.semantic_duplicate_state in {
            SemanticDuplicateState.PASSED,
            SemanticDuplicateState.DUPLICATE_ALLOWED,
        }
        return (
            post.status is PostStatus.STORED and advertisement_ready and duplicate_ready
        )

    @staticmethod
    def _job_id(post_id: str) -> str:
        raw = bytes(
            f"{post_id}|{AITaskType.CATEGORIZATION.value}|"
            f"{_PROMPT_VERSION}|{_SCHEMA_VERSION}",
            encoding="utf-8",
        )
        return f"job_cat_{hashlib.sha256(raw).hexdigest()[:32]}"


__all__ = ("CategorizationRequestContext", "CategorizeWithAI")
