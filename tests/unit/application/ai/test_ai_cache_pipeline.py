"""Focused cache integration tests for the isolated T039/T040 executor."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import BaseModel

from telegram_assist_bot.application.ai.cache_key import (
    AICacheIdentity,
    build_ai_cache_identity,
)
from telegram_assist_bot.application.ai.contracts import (
    AIResult,
    AISideEffectWarningCode,
    AITaskType,
    RawResponseEnvelope,
)
from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
    ExecuteAIWithFallback,
)
from telegram_assist_bot.application.ports.ai_cache_repository import (
    AICacheEntry,
    AICacheRepositoryError,
    AICacheWriteResult,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.shared.config import (
    AiAuditConfig,
    AiCachePolicyConfig,
    AiConfig,
    AiProviderConfig,
    AiProviderGuardConfig,
    AiRouteCandidateConfig,
    AiTaskFailureAction,
    AiTaskFailurePolicyConfig,
    AiTaskRouteConfig,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.application.ports.provider_metrics_repository import (
        ProviderMetricDelta,
        ProviderMetrics,
    )

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


class Context(BaseModel):
    text: str


class Clock:
    def utc_now(self) -> datetime:
        return NOW


class JobRepository:
    def __init__(self, job: AIJob) -> None:
        self.job = job

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.job if self.job.job_id == job_id else None

    async def update(self, job: AIJob) -> None:
        self.job = job


class Guard:
    def __init__(self) -> None:
        self.reservations = 0

    async def execute[T](
        self,
        *,
        provider_name: str,
        model_name: str,
        owner_id: str,
        policy: AiProviderGuardConfig | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        self.reservations += 1
        return await operation()


class Provider:
    def __init__(self) -> None:
        self.calls = 0

    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        self.calls += 1
        payload = json.dumps(
            {
                "is_advertisement": False,
                "confidence": 0.9,
                "reason": "synthetic",
            }
        )
        return RawResponseEnvelope(
            raw_content=json.dumps({"choices": [{"message": {"content": payload}}]}),
            status_code=200,
            headers={},
            latency_seconds=0.1,
            input_tokens=2,
            output_tokens=3,
        )


class Cache:
    def __init__(self, entry: AICacheEntry | None = None, *, fail_write: bool = False):
        self.entry = entry
        self.fail_write = fail_write
        self.reads = 0
        self.writes = 0

    async def get(
        self, identity: AICacheIdentity, *, as_of: datetime
    ) -> AICacheEntry | None:
        self.reads += 1
        return self.entry

    async def put_if_absent(self, entry: AICacheEntry) -> AICacheWriteResult:
        self.writes += 1
        if self.fail_write:
            raise AICacheRepositoryError("ai_cache_write_failed")
        self.entry = entry
        return AICacheWriteResult(entry=entry, created=True)


class FailingAudit:
    async def append(self, event: object) -> bool:
        raise RuntimeError("synthetic audit failure")


class FailingMetrics:
    async def increment(
        self,
        provider_name: str,
        model_name: str,
        delta: ProviderMetricDelta,
    ) -> ProviderMetrics:
        raise RuntimeError("synthetic metrics failure")

    async def get(self, provider_name: str, model_name: str) -> ProviderMetrics | None:
        return None


async def no_sleep(delay: float) -> None:
    return None


def _config() -> AiConfig:
    return AiConfig(
        providers=(AiProviderConfig(name="provider", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="provider",
                        model_name="model",
                        priority=1,
                        timeout_seconds=5,
                        max_attempts=1,
                        guard_policy=AiProviderGuardConfig(
                            concurrency_limit=1,
                            request_limit=10,
                            request_window_seconds=60,
                            reservation_seconds=10,
                            failure_threshold=2,
                            open_seconds=10,
                            rate_limit_cooldown_seconds=None,
                        ),
                    ),
                ),
            ),
        ),
        failure_policies=(
            AiTaskFailurePolicyConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                action=AiTaskFailureAction.MANUAL_REVIEW,
            ),
        ),
        cache_policies=(
            AiCachePolicyConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                enabled=True,
                ttl_seconds=60,
            ),
        ),
    )


def _job() -> AIJob:
    return AIJob(
        job_id="job",
        post_id="post",
        task_type=AITaskType.ADVERTISEMENT_DETECTION.value,
        prompt_version="1",
        schema_version="1",
        idempotency_key="key",
        status=AIJobStatus.PROCESSING,
        priority=1,
        attempts=1,
        max_attempts=3,
        next_run_at=NOW,
        lease_owner="owner",
        lease_expires_at=NOW + timedelta(minutes=1),
        version=1,
    )


def _cached_result() -> AIResult:
    return AIResult(
        success=True,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name="original-provider",
        model_name="original-model",
        result={
            "is_advertisement": False,
            "confidence": 0.9,
            "reason": "synthetic",
        },
        confidence=0.9,
        reason="synthetic",
        prompt_version="1",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
        latency=None,
        input_tokens=None,
        output_tokens=None,
        created_at=NOW - timedelta(seconds=10),
    )


def test_cache_hit_bypasses_guard_and_provider() -> None:
    async def scenario() -> None:
        context = Context(text="متن\u200cآزمایشی")
        identity = build_ai_cache_identity(
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            request_context=context,
            prompt_version="1",
            schema_version="1",
            language="fa",
        )
        cache = Cache(
            AICacheEntry(
                identity=identity,
                result=_cached_result(),
                created_at=NOW - timedelta(seconds=10),
                expires_at=NOW + timedelta(seconds=50),
            )
        )
        provider = Provider()
        guard = Guard()
        repository = JobRepository(_job())
        executor = ExecuteAIWithFallback(
            config=_config(),
            providers_by_name={"provider": provider},  # type: ignore[dict-item]
            ai_job_repository=repository,  # type: ignore[arg-type]
            clock=Clock(),
            sleeper=no_sleep,
            jitter_source=lambda: 0.5,
            provider_guard=guard,
            cache_repository=cache,
        )

        result = await executor.execute(
            "job", "owner", "unused prompt", context, language="fa"
        )
        assert result.cache_hit is True
        assert result.cache_age_seconds == 10
        assert result.provider_name == "original-provider"
        assert provider.calls == 0
        assert guard.reservations == 0
        assert repository.job.status is AIJobStatus.COMPLETED

    import asyncio

    asyncio.run(scenario())


def test_cache_write_failure_preserves_valid_provider_result() -> None:
    async def scenario() -> None:
        provider = Provider()
        guard = Guard()
        cache = Cache(fail_write=True)
        repository = JobRepository(_job())
        executor = ExecuteAIWithFallback(
            config=_config(),
            providers_by_name={"provider": provider},  # type: ignore[dict-item]
            ai_job_repository=repository,  # type: ignore[arg-type]
            clock=Clock(),
            sleeper=no_sleep,
            jitter_source=lambda: 0.5,
            provider_guard=guard,
            cache_repository=cache,
        )

        result = await executor.execute(
            "job", "owner", "synthetic prompt", Context(text="متن"), language="fa"
        )
        assert result.success is True
        assert result.cache_hit is False
        assert result.side_effect_warnings == (
            AISideEffectWarningCode.CACHE_WRITE_FAILED,
        )
        assert provider.calls == 1
        assert guard.reservations == 1
        assert cache.writes == 1

    import asyncio

    asyncio.run(scenario())


def test_disabled_cache_performs_no_cache_read_or_write() -> None:
    async def scenario() -> None:
        provider = Provider()
        cache = Cache()
        repository = JobRepository(_job())
        config = _config().model_copy(update={"cache_policies": ()})
        executor = ExecuteAIWithFallback(
            config=config,
            providers_by_name={"provider": provider},  # type: ignore[dict-item]
            ai_job_repository=repository,  # type: ignore[arg-type]
            clock=Clock(),
            sleeper=no_sleep,
            jitter_source=lambda: 0.5,
            provider_guard=Guard(),
            cache_repository=cache,
        )

        result = await executor.execute(
            "job", "owner", "synthetic prompt", Context(text="متن"), language="fa"
        )
        assert result.success is True
        assert provider.calls == 1
        assert cache.reads == 0
        assert cache.writes == 0

    import asyncio

    asyncio.run(scenario())


def test_audit_and_metrics_failures_are_sanitized_nonfatal_warnings() -> None:
    async def scenario() -> None:
        provider = Provider()
        repository = JobRepository(_job())
        config = _config().model_copy(
            update={
                "audit": AiAuditConfig(enabled=True, retention_seconds=60),
            }
        )
        executor = ExecuteAIWithFallback(
            config=config,
            providers_by_name={"provider": provider},  # type: ignore[dict-item]
            ai_job_repository=repository,  # type: ignore[arg-type]
            clock=Clock(),
            sleeper=no_sleep,
            jitter_source=lambda: 0.5,
            provider_guard=Guard(),
            cache_repository=Cache(),
            audit_repository=FailingAudit(),
            metrics_repository=FailingMetrics(),
        )

        result = await executor.execute(
            "job", "owner", "synthetic prompt", Context(text="متن"), language="fa"
        )
        assert result.success is True
        assert set(result.side_effect_warnings) == {
            AISideEffectWarningCode.AUDIT_APPEND_FAILED,
            AISideEffectWarningCode.METRICS_INCREMENT_FAILED,
        }
        assert all(
            "synthetic" not in warning.value for warning in result.side_effect_warnings
        )

    import asyncio

    asyncio.run(scenario())
