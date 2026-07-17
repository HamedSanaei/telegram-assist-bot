"""Unit tests for AI routing, retrying, and fallback orchestration pipeline."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import BaseModel

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
    AllProvidersFailedError,
    ExecuteAIWithFallback,
)
from telegram_assist_bot.application.ports import (
    AIJobConcurrencyConflictError,
    AIJobNotFoundError,
    AIJobRepository,
    AIProvider,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.shared.config import (
    AiConfig,
    AiProviderConfig,
    AiQueueConfig,
    AiRouteCandidateConfig,
    AiTaskFailureAction,
    AiTaskFailurePolicyConfig,
    AiTaskRouteConfig,
)
from telegram_assist_bot.shared.errors import (
    ConfigurationError,
    PermissionDeniedError,
    TransientOperationError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence

    from telegram_assist_bot.application.ports.ai_job_repository import (
        EnqueueJobResult,
    )
    from telegram_assist_bot.shared.config import AiProviderGuardConfig


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


class FakeClock:
    """Mock clock that progresses time deterministically in tests."""

    def __init__(self, start_time: datetime | None = None) -> None:
        self.current = start_time or datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

    def utc_now(self) -> datetime:
        return self.current

    def advance(self, duration: timedelta) -> None:
        self.current += duration


class FakeSleeper:
    """Fake sleeper that records sleep durations without blocking."""

    def __init__(self) -> None:
        self.sleeps: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.sleeps.append(delay)


class FakeJitter:
    """Deterministic jitter source returning a constant ratio."""

    def __init__(self, ratio: float = 0.5) -> None:
        self.ratio = ratio

    def __call__(self) -> float:
        return self.ratio


class PassThroughProviderGuard:
    """Test guard preserving legacy T039 scenarios without external state."""

    async def execute[T](
        self,
        *,
        provider_name: str,
        model_name: str,
        owner_id: str,
        policy: AiProviderGuardConfig | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        return await operation()


class FakeAIProvider(AIProvider):
    """Fake AI provider configured to return canned responses or raise errors."""

    def __init__(self, behavior: Sequence[RawResponseEnvelope | BaseException]) -> None:
        self.behavior = list(behavior)
        self.calls: list[dict[str, Any]] = []

    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        self.calls.append(
            {
                "task_type": task_type,
                "provider_name": provider_name,
                "model_name": model_name,
                "timeout_seconds": timeout_seconds,
            }
        )
        if not self.behavior:
            raise RuntimeError("FakeAIProvider has no programmed behavior left.")

        item = self.behavior.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


class FakeAIJobRepository(AIJobRepository):
    """In-memory repository for AIJobs checking versioning and ownership."""

    def __init__(self, jobs: dict[str, AIJob]) -> None:
        self.jobs = jobs

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        self.jobs[job.job_id] = job
        from telegram_assist_bot.application.ports.ai_job_repository import (
            EnqueueJobOutcome,
            EnqueueJobResult,
        )

        return EnqueueJobResult(outcome=EnqueueJobOutcome.CREATED, job=job)

    async def claim_next_due(
        self,
        owner: str,
        lease_duration_seconds: float,
        as_of: datetime,
    ) -> AIJob | None:
        return None

    async def update(self, job: AIJob) -> None:
        existing = self.jobs.get(job.job_id)
        if not existing:
            raise AIJobNotFoundError(f"Job {job.job_id} not found")
        # Check concurrency conflict
        if existing.version != job.version - 1:
            raise AIJobConcurrencyConflictError(
                f"Version mismatch: expected {job.version - 1}, "
                f"found {existing.version}"
            )
        self.jobs[job.job_id] = job

    async def get_by_id(self, job_id: str) -> AIJob | None:
        return self.jobs.get(job_id)

    async def get_by_key(self, idempotency_key: str) -> AIJob | None:
        for job in self.jobs.values():
            if job.idempotency_key == idempotency_key:
                return job
        return None

    async def initialize_indexes(self) -> None:
        pass


class DummyContext(BaseModel):
    text: str


def make_raw_envelope(
    content: str,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> RawResponseEnvelope:
    """Helper to construct a RawResponseEnvelope with openAI choices structure."""
    import json

    raw_body = json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "content": content,
                    }
                }
            ]
        }
    )
    return RawResponseEnvelope(
        raw_content=raw_body,
        status_code=200,
        headers={},
        latency_seconds=0.1,
        input_tokens=prompt_tokens,
        output_tokens=completion_tokens,
    )


@async_test
async def test_fallback_to_second_provider_on_failure() -> None:
    """Verifies pipeline moves to the next provider/candidate if the first fails."""
    config = AiConfig(
        queue=AiQueueConfig(
            lease_duration_seconds=60, max_attempts=3, next_run_delay_seconds=30
        ),
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    # Candidate 1: z-ai (max_attempts = 2)
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=2,
                    ),
                    # Candidate 2: deepseek (max_attempts = 1)
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=1,
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

    # Behaviors:
    # 1. z-ai attempt 1: Transient transport exception (retryable)
    # 2. z-ai attempt 2: Timeout (max attempts reached -> deepseek)
    # 3. deepseek attempt 1: Success!
    behavior_zai = [
        TransientOperationError(cause=ConnectionError("Transient network drop")),
        TimeoutError("Timeout contacting server"),
    ]
    behavior_deepseek = [
        make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.9, "reason": "ad"}'
        )
    ]

    provider_zai = FakeAIProvider(behavior_zai)
    provider_deepseek = FakeAIProvider(behavior_deepseek)

    providers = {"z-ai": provider_zai, "deepseek": provider_deepseek}

    # Setup claimed job
    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    clock = FakeClock()
    sleeper = FakeSleeper()
    jitter = FakeJitter()

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=clock,
        sleeper=sleeper,
        jitter_source=jitter,
        provider_guard=PassThroughProviderGuard(),
    )

    result = await orchestrator.execute(
        job_id="job-123",
        owner="worker-1",
        prompt_text="Detect ads",
        request_context=DummyContext(text="free cash!"),
    )

    assert result is not None
    assert result.payload is not None
    assert result.payload["is_advertisement"] is True
    assert result.provider_name == "deepseek"
    assert result.model_name == "deepseek-v4-flash"

    # Verify repository job state
    updated_job = repo.jobs["job-123"]
    assert updated_job.status == AIJobStatus.COMPLETED
    assert updated_job.result == result.payload

    # Verify telemetry
    # attempted count 2
    # fallback count 1
    # retry count 1 (z-ai second attempt was a retry)
    assert updated_job.attempted_candidates_count == 2
    assert updated_job.fallback_count == 1
    assert updated_job.retry_count == 1

    # Verify attempts history matches:
    # 1. z-ai attempt 1
    # 2. z-ai attempt 2
    # 3. deepseek attempt 1
    history = updated_job.attempts_history
    assert history is not None
    assert len(history) == 3
    assert history[0]["provider_name"] == "z-ai"
    assert history[0]["success"] is False
    assert history[0]["failure_category"] == "transient"
    assert history[1]["provider_name"] == "z-ai"
    assert history[1]["success"] is False
    assert history[1]["failure_category"] == "timeout"
    assert history[2]["provider_name"] == "deepseek"
    assert history[2]["success"] is True


@async_test
async def test_non_retryable_failure_causes_immediate_fallback() -> None:
    """Verifies non-retryable failures skip retry and fallback."""
    config = AiConfig(
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    # Candidate 1: z-ai (max_attempts = 5)
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=5,
                    ),
                    # Candidate 2: deepseek (max_attempts = 1)
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=1,
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

    # Behaviors:
    # 1. z-ai attempt 1: PermissionError / Auth failure (skips retry, falls back)
    # 2. deepseek attempt 1: Success
    behavior_zai = [
        PermissionDeniedError(cause=PermissionError("Incorrect API Key supplied")),
    ]
    behavior_deepseek = [
        make_raw_envelope(
            '{"is_advertisement": false, "confidence": 0.8, "reason": "normal"}'
        )
    ]

    provider_zai = FakeAIProvider(behavior_zai)
    provider_deepseek = FakeAIProvider(behavior_deepseek)

    providers = {"z-ai": provider_zai, "deepseek": provider_deepseek}

    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    result = await orchestrator.execute(
        job_id="job-123",
        owner="worker-1",
        prompt_text="Detect ads",
        request_context=DummyContext(text="normal content"),
    )

    assert result.provider_name == "deepseek"
    updated_job = repo.jobs["job-123"]
    assert updated_job.status == AIJobStatus.COMPLETED
    assert len(updated_job.attempts_history or []) == 2
    assert updated_job.attempts_history is not None
    assert updated_job.attempts_history[0]["failure_category"] == "permission"


@async_test
async def test_invalid_t038_schema_causes_fallback() -> None:
    """Verifies that parsed but invalid outputs trigger fallback immediately."""
    config = AiConfig(
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    # Candidate 1: z-ai (max_attempts = 3)
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                    # Candidate 2: deepseek (max_attempts = 1)
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=1,
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

    # Behaviors:
    # 1. z-ai attempt 1: Returns invalid schemas.
    #    This should NOT retry on z-ai; falls back to deepseek immediately!
    # 2. deepseek attempt 1: Success
    behavior_zai = [
        make_raw_envelope(
            '{"reason": "missing fields like is_advertisement and confidence"}'
        )
    ]
    behavior_deepseek = [
        make_raw_envelope(
            '{"is_advertisement": false, "confidence": 0.8, "reason": "normal"}'
        )
    ]

    provider_zai = FakeAIProvider(behavior_zai)
    provider_deepseek = FakeAIProvider(behavior_deepseek)

    providers = {"z-ai": provider_zai, "deepseek": provider_deepseek}

    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    result = await orchestrator.execute(
        job_id="job-123",
        owner="worker-1",
        prompt_text="Detect ads",
        request_context=DummyContext(text="content"),
    )

    assert result.provider_name == "deepseek"
    updated_job = repo.jobs["job-123"]
    assert updated_job.status == AIJobStatus.COMPLETED
    assert len(updated_job.attempts_history or []) == 2
    assert updated_job.attempts_history is not None
    assert updated_job.attempts_history[0]["failure_category"] == "validation"


@async_test
async def test_first_valid_result_stops_execution() -> None:
    """Verifies that the orchestrator stops at first valid result."""
    config = AiConfig(
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    # Candidate 1: z-ai (Success)
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
                    ),
                    # Candidate 2: deepseek (Should NOT be called)
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=3,
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

    behavior_zai = [
        make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.9, "reason": "ad"}'
        )
    ]
    behavior_deepseek: list[
        RawResponseEnvelope
    ] = []  # Empty behavior, raises if called

    provider_zai = FakeAIProvider(behavior_zai)
    provider_deepseek = FakeAIProvider(behavior_deepseek)

    providers = {"z-ai": provider_zai, "deepseek": provider_deepseek}

    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    result = await orchestrator.execute(
        job_id="job-123",
        owner="worker-1",
        prompt_text="Detect ads",
        request_context=DummyContext(text="content"),
    )

    assert result.provider_name == "z-ai"
    assert len(provider_deepseek.calls) == 0  # Not called!


@async_test
async def test_all_candidates_failing_raises_all_providers_failed_error() -> None:
    """Verifies that AllProvidersFailedError is raised on final failure."""
    config = AiConfig(
        queue=AiQueueConfig(
            lease_duration_seconds=60, max_attempts=3, next_run_delay_seconds=30
        ),
        providers=(
            AiProviderConfig(name="z-ai", enabled=True),
            AiProviderConfig(name="deepseek", enabled=True),
        ),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    # Candidate 1: z-ai
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=1,
                    ),
                    # Candidate 2: deepseek
                    AiRouteCandidateConfig(
                        provider_name="deepseek",
                        model_name="deepseek-v4-flash",
                        priority=2,
                        timeout_seconds=30,
                        max_attempts=1,
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

    behavior_zai = [TransientOperationError(cause=ConnectionError("z-ai down"))]
    behavior_deepseek = [TimeoutError("deepseek timed out")]

    provider_zai = FakeAIProvider(behavior_zai)
    provider_deepseek = FakeAIProvider(behavior_deepseek)

    providers = {"z-ai": provider_zai, "deepseek": provider_deepseek}

    # Job is on its final job-level attempt (attempts=3, max_attempts=3)
    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=3,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    with pytest.raises(AllProvidersFailedError) as exc_info:
        await orchestrator.execute(
            job_id="job-123",
            owner="worker-1",
            prompt_text="Detect ads",
            request_context=DummyContext(text="content"),
        )

    assert exc_info.value.action == AiTaskFailureAction.MANUAL_REVIEW
    assert "All AI candidates failed" in str(exc_info.value)

    # Verify repository job has transitioned to ALL_PROVIDERS_FAILED
    updated_job = repo.jobs["job-123"]
    assert updated_job.status == AIJobStatus.ALL_PROVIDERS_FAILED
    assert updated_job.safe_last_failure_code == "timeout"
    assert len(updated_job.attempts_history or []) == 2


@async_test
async def test_all_candidates_failing_with_retry_available() -> None:
    """Verifies final failure sets status to WaitingForRetry if retry available."""
    config = AiConfig(
        queue=AiQueueConfig(
            lease_duration_seconds=60, max_attempts=3, next_run_delay_seconds=30
        ),
        providers=(AiProviderConfig(name="z-ai", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=1,
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

    behavior_zai = [TransientOperationError(cause=ConnectionError("z-ai down"))]

    provider_zai = FakeAIProvider(behavior_zai)

    providers = {"z-ai": provider_zai}

    # Job-level attempts=1, max_attempts=3 (retry is available)
    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    with pytest.raises(AllProvidersFailedError):
        await orchestrator.execute(
            job_id="job-123",
            owner="worker-1",
            prompt_text="Detect ads",
            request_context=DummyContext(text="content"),
        )

    # Job should transition to WAITING_FOR_RETRY
    updated_job = repo.jobs["job-123"]
    assert updated_job.status == AIJobStatus.WAITING_FOR_RETRY
    assert updated_job.safe_last_failure_code == "transient"
    assert len(updated_job.attempts_history or []) == 1


@async_test
async def test_execute_raises_error_on_missing_failure_policy() -> None:
    """Verifies missing failure policy raises ConfigurationError."""
    config = AiConfig(
        providers=(AiProviderConfig(name="z-ai", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=1,
                    ),
                ),
            ),
        ),
        # Empty failure policies
        failure_policies=(),
    )

    providers = {"z-ai": FakeAIProvider([])}

    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    with pytest.raises(ConfigurationError) as exc_info:
        await orchestrator.execute(
            job_id="job-123",
            owner="worker-1",
            prompt_text="Detect ads",
            request_context=DummyContext(text="content"),
        )
    assert "No failure policy configured" in str(exc_info.value.__cause__)


@async_test
async def test_execute_raises_concurrency_conflict_on_stale_job() -> None:
    """Verifies that execute raises conflict error on version mismatch."""
    config = AiConfig(
        providers=(AiProviderConfig(name="z-ai", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=1,
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

    behavior_zai = [
        make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.9, "reason": "ad"}'
        )
    ]
    provider_zai = FakeAIProvider(behavior_zai)

    providers = {"z-ai": provider_zai}

    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )

    class ConflictRepository(FakeAIJobRepository):
        async def get_by_id(self, job_id: str) -> AIJob | None:
            job_obj = await super().get_by_id(job_id)
            if job_obj:
                return replace(job_obj, version=1)
            return None

    repo = ConflictRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    # Simulating concurrency conflict
    repo.jobs["job-123"] = replace(job, version=5)

    with pytest.raises(AIJobConcurrencyConflictError):
        await orchestrator.execute(
            job_id="job-123",
            owner="worker-1",
            prompt_text="Detect ads",
            request_context=DummyContext(text="content"),
        )


@async_test
async def test_cancellation_propagates_immediately() -> None:
    """Verifies that cancellation propagates immediately."""
    config = AiConfig(
        providers=(AiProviderConfig(name="z-ai", enabled=True),),
        routes=(
            AiTaskRouteConfig(
                task=AITaskType.ADVERTISEMENT_DETECTION,
                candidates=(
                    AiRouteCandidateConfig(
                        provider_name="z-ai",
                        model_name="glm-4.7-flash",
                        priority=1,
                        timeout_seconds=30,
                        max_attempts=3,
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

    # Behaviors:
    # 1. z-ai attempt 1 raises CancelledError
    provider_zai = FakeAIProvider([asyncio.CancelledError("User cancelled")])
    providers = {"z-ai": provider_zai}

    job = AIJob(
        job_id="job-123",
        post_id="post-abc",
        task_type="advertisement_detection",
        prompt_version="1",
        schema_version="1",
        idempotency_key="post-abc:advertisement_detection:1:1",
        status=AIJobStatus.PROCESSING,
        priority=20,
        attempts=1,
        max_attempts=3,
        next_run_at=datetime.now(UTC),
        lease_owner="worker-1",
        lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
        version=1,
    )
    repo = FakeAIJobRepository({"job-123": job})

    orchestrator = ExecuteAIWithFallback(
        config=config,
        providers_by_name=providers,
        ai_job_repository=repo,
        clock=FakeClock(),
        sleeper=FakeSleeper(),
        jitter_source=FakeJitter(),
        provider_guard=PassThroughProviderGuard(),
    )

    with pytest.raises(asyncio.CancelledError):
        await orchestrator.execute(
            job_id="job-123",
            owner="worker-1",
            prompt_text="Detect ads",
            request_context=DummyContext(text="content"),
        )

    # Job is left in processing and not completed/failed
    assert repo.jobs["job-123"].status == AIJobStatus.PROCESSING
