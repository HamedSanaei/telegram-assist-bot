"""Unit tests for the per-attempt ProviderGuard application service."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.provider_guard import (
    AllProvidersTemporarilyUnavailableError,
    ProviderGuard,
    ProviderTemporarilyUnavailableError,
    outcome_from_error,
)
from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
    ExecuteAIWithFallback,
)
from telegram_assist_bot.application.ports.ai_provider import AIProvider
from telegram_assist_bot.application.ports.provider_state_repository import (
    ProviderReservationResult,
    ProviderStateRepository,
)
from telegram_assist_bot.domain.ai.provider_health import (
    ActiveReservation,
    IneligibilityReason,
    ProviderAttemptOutcome,
    ProviderFailureCategory,
    ProviderHealth,
    ReservationKind,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.shared.config import (
    AiConfig,
    AiProviderConfig,
    AiProviderGuardConfig,
    AiRouteCandidateConfig,
    AiTaskFailureAction,
    AiTaskFailurePolicyConfig,
    AiTaskRouteConfig,
)
from telegram_assist_bot.shared.errors import (
    AuthorizationError,
    ConfigurationError,
    OperationTimeoutError,
    RateLimitError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

NOW = datetime(2026, 7, 18, 9, 0, tzinfo=UTC)


class FixedClock:
    """Return one deterministic UTC time."""

    def utc_now(self) -> datetime:
        return NOW


class DummyContext(BaseModel):
    """Minimal request context for router integration tests."""

    text: str


class CountingProvider(AIProvider):
    """Return one valid response while counting actual external attempts."""

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
        return RawResponseEnvelope(
            raw_content=json.dumps(
                {
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "is_advertisement": False,
                                        "confidence": 0.9,
                                        "reason": "synthetic",
                                    }
                                )
                            }
                        }
                    ]
                }
            ),
            status_code=200,
            headers={},
            latency_seconds=0.1,
            input_tokens=1,
            output_tokens=1,
        )


class JobRepository:
    """Minimal owned job persistence for router integration tests."""

    def __init__(self, job: AIJob) -> None:
        self.job = job

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.job if self.job.job_id == job_id else None

    async def update(self, job: AIJob) -> None:
        self.job = job


class NoSleep:
    """Deterministic retry sleeper."""

    async def __call__(self, delay: float) -> None:
        return None


class SelectiveGuard:
    """Reject selected providers with deterministic eligibility timestamps."""

    def __init__(self, unavailable: dict[str, datetime]) -> None:
        self.unavailable = unavailable

    async def execute[T](
        self,
        *,
        provider_name: str,
        model_name: str,
        owner_id: str,
        policy: AiProviderGuardConfig | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        unavailable_until = self.unavailable.get(provider_name)
        if unavailable_until is not None:
            raise ProviderTemporarilyUnavailableError(
                "cooldown_active",
                unavailable_until,
            )
        return await operation()


class FakeRepository(ProviderStateRepository):
    """Record guard calls without external persistence."""

    def __init__(self, *, allowed: bool = True) -> None:
        self.allowed = allowed
        self.recorded: list[ProviderAttemptOutcome] = []
        self.reserve_calls = 0

    async def get_or_create(
        self, provider_name: str, model_name: str
    ) -> ProviderHealth:
        return ProviderHealth(provider_name, model_name)

    async def reserve(
        self,
        provider_name: str,
        model_name: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
        *,
        concurrency_limit: int,
        request_window_seconds: int,
        request_limit: int,
    ) -> ProviderReservationResult:
        self.reserve_calls += 1
        if not self.allowed:
            return ProviderReservationResult(
                None,
                IneligibilityReason.COOLDOWN_ACTIVE,
                NOW + timedelta(seconds=5),
            )
        return ProviderReservationResult(
            ActiveReservation(
                "reservation",
                owner_id,
                ReservationKind.NORMAL,
                now,
                expires_at,
            )
        )

    async def record(
        self,
        provider_name: str,
        model_name: str,
        reservation_id: str,
        owner_id: str,
        outcome: ProviderAttemptOutcome,
        now: datetime,
        *,
        failure_threshold: int,
        open_seconds: int,
        fallback_cooldown_seconds: int | None,
    ) -> ProviderHealth:
        self.recorded.append(outcome)
        return ProviderHealth(provider_name, model_name)


def guard_policy() -> AiProviderGuardConfig:
    """Build a fully explicit synthetic guard policy."""
    return AiProviderGuardConfig(
        concurrency_limit=2,
        request_limit=5,
        request_window_seconds=60,
        reservation_seconds=30,
        failure_threshold=2,
        open_seconds=10,
        rate_limit_cooldown_seconds=None,
    )


def test_missing_policy_fails_before_operation() -> None:
    async def scenario() -> None:
        repository = FakeRepository()
        called = False

        async def operation() -> None:
            nonlocal called
            called = True

        with pytest.raises(ConfigurationError):
            await ProviderGuard(repository, FixedClock()).execute(
                provider_name="provider",
                model_name="model",
                owner_id="owner",
                policy=None,
                operation=operation,
            )
        assert called is False
        assert repository.reserve_calls == 0

    asyncio.run(scenario())


def test_unavailable_candidate_does_not_call_provider() -> None:
    async def scenario() -> None:
        repository = FakeRepository(allowed=False)
        called = False

        async def operation() -> None:
            nonlocal called
            called = True

        with pytest.raises(ProviderTemporarilyUnavailableError) as exc:
            await ProviderGuard(repository, FixedClock()).execute(
                provider_name="provider",
                model_name="model",
                owner_id="owner",
                policy=guard_policy(),
                operation=operation,
            )
        assert called is False
        assert exc.value.reason == "cooldown_active"
        assert exc.value.next_eligible_at == NOW + timedelta(seconds=5)

    asyncio.run(scenario())


def test_success_and_timeout_record_typed_outcomes() -> None:
    async def scenario() -> None:
        success_repository = FakeRepository()

        async def success() -> str:
            return "ok"

        result = await ProviderGuard(success_repository, FixedClock()).execute(
            provider_name="provider",
            model_name="model",
            owner_id="owner",
            policy=guard_policy(),
            operation=success,
        )
        assert result == "ok"
        assert success_repository.recorded == [ProviderAttemptOutcome.succeeded()]

        timeout_repository = FakeRepository()

        async def timeout() -> None:
            raise OperationTimeoutError

        with pytest.raises(OperationTimeoutError):
            await ProviderGuard(timeout_repository, FixedClock()).execute(
                provider_name="provider",
                model_name="model",
                owner_id="owner",
                policy=guard_policy(),
                operation=timeout,
            )
        outcome = timeout_repository.recorded[0]
        assert outcome.failure_category is ProviderFailureCategory.TIMEOUT
        assert outcome.health_failure is True

    asyncio.run(scenario())


def test_cancellation_is_recorded_without_health_failure() -> None:
    async def scenario() -> None:
        repository = FakeRepository()

        async def cancel() -> None:
            raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await ProviderGuard(repository, FixedClock()).execute(
                provider_name="provider",
                model_name="model",
                owner_id="owner",
                policy=guard_policy(),
                operation=cancel,
            )
        assert repository.recorded[0].cancelled is True
        assert repository.recorded[0].health_failure is False

    asyncio.run(scenario())


def test_outcome_mapping_never_invents_rate_limit_metadata() -> None:
    auth = outcome_from_error(AuthorizationError())
    assert auth.failure_category is ProviderFailureCategory.AUTHORIZATION
    assert auth.health_failure is False
    assert auth.rate_limited is False

    rate_error = RateLimitError()
    rate = outcome_from_error(rate_error)
    assert rate.rate_limited is True
    assert rate.retry_after_seconds is None

    object.__setattr__(rate_error, "retry_after", "12")
    with_metadata = outcome_from_error(rate_error)
    assert with_metadata.retry_after_seconds == 12


def _router_config() -> AiConfig:
    policy = guard_policy()
    return AiConfig(
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=5,
                        max_attempts=2,
                        guard_policy=policy,
                    ),
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=5,
                        max_attempts=2,
                        guard_policy=policy,
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
    )


def _claimed_job() -> AIJob:
    return AIJob(
        job_id="job",
        post_id="post",
        task_type=AITaskType.ADVERTISEMENT_DETECTION.value,
        prompt_version="1",
        schema_version="1",
        idempotency_key="job-key",
        status=AIJobStatus.PROCESSING,
        priority=1,
        attempts=1,
        max_attempts=3,
        next_run_at=NOW,
        lease_owner="owner",
        lease_expires_at=NOW + timedelta(minutes=1),
        version=1,
    )


def test_router_skips_unavailable_candidate_without_provider_call_or_attempt() -> None:
    async def scenario() -> None:
        first = CountingProvider()
        second = CountingProvider()
        repository = JobRepository(_claimed_job())
        executor = ExecuteAIWithFallback(
            config=_router_config(),
            providers_by_name={"z-ai": first, "deepseek": second},
            ai_job_repository=repository,  # type: ignore[arg-type]
            clock=FixedClock(),
            sleeper=NoSleep(),
            jitter_source=lambda: 0.5,
            provider_guard=SelectiveGuard({"z-ai": NOW + timedelta(seconds=30)}),
        )

        result = await executor.execute(
            "job",
            "owner",
            "synthetic prompt",
            DummyContext(text="synthetic"),
        )
        assert result.success is True
        assert first.calls == 0
        assert second.calls == 1
        assert repository.job.attempted_candidates_count == 1

    asyncio.run(scenario())


def test_router_reports_nearest_time_when_all_candidates_unavailable() -> None:
    async def scenario() -> None:
        first = CountingProvider()
        second = CountingProvider()
        repository = JobRepository(_claimed_job())
        executor = ExecuteAIWithFallback(
            config=_router_config(),
            providers_by_name={"z-ai": first, "deepseek": second},
            ai_job_repository=repository,  # type: ignore[arg-type]
            clock=FixedClock(),
            sleeper=NoSleep(),
            jitter_source=lambda: 0.5,
            provider_guard=SelectiveGuard(
                {
                    "z-ai": NOW + timedelta(seconds=30),
                    "deepseek": NOW + timedelta(seconds=10),
                }
            ),
        )

        with pytest.raises(AllProvidersTemporarilyUnavailableError) as exc:
            await executor.execute(
                "job",
                "owner",
                "synthetic prompt",
                DummyContext(text="synthetic"),
            )
        assert exc.value.next_eligible_at == NOW + timedelta(seconds=10)
        assert first.calls == 0
        assert second.calls == 0
        assert repository.job.status is AIJobStatus.PROCESSING

    asyncio.run(scenario())
