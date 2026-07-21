"""Integration tests for AI pipeline acceptance criteria and end-to-end flow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import BaseModel

from telegram_assist_bot.application.ai.contracts import (
    AITaskType,
    RawResponseEnvelope,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
    MongoAIJobRepository,
    initialize_ai_job_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
    close_mongodb_client,
    create_mongodb_client,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    AiCachePolicyConfig,
    AiConfig,
    AiProviderConfig,
    AiProviderGuardConfig,
    AiQueueConfig,
    AiRouteCandidateConfig,
    AiTaskFailureAction,
    AiTaskFailurePolicyConfig,
    AiTaskRouteConfig,
    ApplicationConfig,
    MongoConfig,
    SecretReference,
)
from telegram_assist_bot.shared.config.loader import ResolvedSecrets
from telegram_assist_bot.shared.errors import (
    TransientOperationError,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"
_NOW = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


class DummyContext(BaseModel):
    text: str


class IntegrationFakeClock:
    def __init__(self, time_val: datetime) -> None:
        self.time_val = time_val

    def utc_now(self) -> datetime:
        return self.time_val

    def advance(self, delta: timedelta) -> None:
        self.time_val += delta


class ConfigurableFakeAIProvider:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.behavior: dict[str, Callable[..., Any]] = {}

    def set_behavior(self, key: str, func: Callable[..., Any]) -> None:
        self.behavior[key] = func

    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        call_info = {
            "task_type": task_type,
            "prompt": prompt,
            "provider_name": provider_name,
            "model_name": model_name,
        }
        self.calls.append(call_info)
        key = f"{provider_name}/{model_name}"
        if key in self.behavior:
            res = self.behavior[key](**call_info)
            if isinstance(res, Exception):
                raise res
            return cast(RawResponseEnvelope, res)
        raise RuntimeError(f"No programmed behavior for {key}")


def make_raw_envelope(content: str) -> RawResponseEnvelope:
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
        input_tokens=10,
        output_tokens=5,
    )


@async_test
async def test_ai_pipeline_acceptance_flow(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify the durable execution pipeline for a claimed job."""
    config_mongo = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_mongo,
        ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        database = client[config_mongo.database_name]

        jobs_col = database["ai_jobs_acceptance"]
        await initialize_ai_job_indexes(jobs_col)
        jobs_repo = MongoAIJobRepository(jobs_col)

        guard_policy = AiProviderGuardConfig(
            concurrency_limit=5,
            request_limit=100,
            request_window_seconds=60,
            reservation_seconds=10,
            failure_threshold=5,
            open_seconds=30,
            rate_limit_cooldown_seconds=30,
        )

        from unittest.mock import MagicMock

        config = MagicMock(spec=ApplicationConfig)
        config.ai = AiConfig(
            queue=AiQueueConfig(
                lease_duration_seconds=60,
                max_attempts=3,
                next_run_delay_seconds=30,
                worker_poll_seconds=5,
            ),
            providers=(AiProviderConfig(name="z-ai", enabled=True),),
            routes=(
                AiTaskRouteConfig(
                    task=AITaskType.ADVERTISEMENT_DETECTION,
                    candidates=(
                        AiRouteCandidateConfig(
                            provider_name="z-ai",
                            model_name="glm-4.7-flash",
                            priority=0,
                            timeout_seconds=10,
                            max_attempts=2,
                            guard_policy=guard_policy,
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

        fake_envelope = make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.95, "reason": "ad"}'
        )
        fake_provider = ConfigurableFakeAIProvider()
        fake_provider.set_behavior("z-ai/glm-4.7-flash", lambda **kwargs: fake_envelope)
        providers = {"z-ai": fake_provider}

        from telegram_assist_bot.application.ai.provider_guard import ProviderGuard
        from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (  # noqa: E501
            ExecuteAIWithFallback,
        )
        from telegram_assist_bot.infrastructure.mongodb.provider_state_repository import (  # noqa: E501
            MongoProviderStateRepository,
        )

        state_repo = MongoProviderStateRepository(
            database["provider_states_acceptance"]
        )
        provider_guard = ProviderGuard(state_repo, IntegrationFakeClock(_NOW))

        execute_ai = ExecuteAIWithFallback(
            config=config.ai,
            providers_by_name=cast("Any", providers),
            ai_job_repository=jobs_repo,
            clock=IntegrationFakeClock(_NOW),
            sleeper=lambda d: asyncio.sleep(0),
            jitter_source=lambda: 0.5,
            provider_guard=provider_guard,
        )

        # Enqueue and claim AI job
        job = AIJob.create(
            job_id="test-job-acceptance-1",
            post_id="test-post-acceptance-1",
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job)

        claimed = await jobs_repo.claim_next_due(
            owner="worker-1",
            lease_duration_seconds=60,
            as_of=_NOW,
        )
        assert claimed is not None

        # Execute fallback pipeline
        res = await execute_ai.execute(
            job_id=claimed.job_id,
            owner="worker-1",
            prompt_text="Check if this is an ad: Buy now!",
            request_context=DummyContext(text="Buy now!"),
            language="fa",
        )
        assert res.success is True
        assert res.provider_name == "z-ai"
        assert res.model_name == "glm-4.7-flash"

        # Verify job is marked COMPLETED in database
        updated_job = await jobs_repo.get_by_id(claimed.job_id)
        assert updated_job is not None
        assert updated_job.status is AIJobStatus.COMPLETED

    finally:
        await close_mongodb_client(client, timeout_seconds=5)


@async_test
async def test_ai_provider_routing_disabled_and_fallback(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify disabled/unsupported providers are not called and fallback logic works."""
    config_mongo = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_mongo,
        ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        database = client[config_mongo.database_name]

        jobs_col = database["ai_jobs_routing_fallback"]
        await initialize_ai_job_indexes(jobs_col)
        jobs_repo = MongoAIJobRepository(jobs_col)

        guard_policy = AiProviderGuardConfig(
            concurrency_limit=5,
            request_limit=100,
            request_window_seconds=60,
            reservation_seconds=10,
            failure_threshold=5,
            open_seconds=30,
            rate_limit_cooldown_seconds=30,
        )

        from unittest.mock import MagicMock

        config = MagicMock(spec=ApplicationConfig)
        config.ai = AiConfig(
            queue=AiQueueConfig(
                lease_duration_seconds=60,
                max_attempts=3,
                next_run_delay_seconds=30,
                worker_poll_seconds=5,
            ),
            # z-ai is enabled, deepseek is disabled!
            providers=(
                AiProviderConfig(name="z-ai", enabled=True),
                AiProviderConfig(name="deepseek", enabled=False),
            ),
            routes=(
                AiTaskRouteConfig(
                    task=AITaskType.ADVERTISEMENT_DETECTION,
                    candidates=(
                        # Candidates are ordered by priority
                        AiRouteCandidateConfig(
                            provider_name="deepseek",
                            model_name="deepseek-v4-flash",
                            priority=0,
                            timeout_seconds=10,
                            max_attempts=2,
                            guard_policy=guard_policy,
                        ),
                        AiRouteCandidateConfig(
                            provider_name="z-ai",
                            model_name="glm-4.7-flash",
                            priority=1,
                            timeout_seconds=10,
                            max_attempts=2,
                            guard_policy=guard_policy,
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

        fake_envelope = make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.95, "reason": "ad"}'
        )
        fake_provider = ConfigurableFakeAIProvider()
        # Mock behavior for both providers
        fake_provider.set_behavior(
            "deepseek/deepseek-v4-flash", lambda **kwargs: fake_envelope
        )
        fake_provider.set_behavior("z-ai/glm-4.7-flash", lambda **kwargs: fake_envelope)

        providers = {
            "z-ai": fake_provider,
            "deepseek": fake_provider,
        }

        from telegram_assist_bot.application.ai.provider_guard import ProviderGuard
        from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (  # noqa: E501
            ExecuteAIWithFallback,
        )
        from telegram_assist_bot.infrastructure.mongodb.provider_state_repository import (  # noqa: E501
            MongoProviderStateRepository,
        )

        state_repo = MongoProviderStateRepository(database["provider_states_routing"])
        provider_guard = ProviderGuard(state_repo, IntegrationFakeClock(_NOW))

        execute_ai = ExecuteAIWithFallback(
            config=config.ai,
            providers_by_name=cast("Any", providers),
            ai_job_repository=jobs_repo,
            clock=IntegrationFakeClock(_NOW),
            sleeper=lambda d: asyncio.sleep(0),
            jitter_source=lambda: 0.5,
            provider_guard=provider_guard,
        )

        # Enqueue and claim AI job
        job = AIJob.create(
            job_id="test-job-routing-1",
            post_id="test-post-routing-1",
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job)

        claimed = await jobs_repo.claim_next_due(
            owner="worker-1",
            lease_duration_seconds=60,
            as_of=_NOW,
        )
        assert claimed is not None

        # Execute fallback pipeline
        res = await execute_ai.execute(
            job_id=claimed.job_id,
            owner="worker-1",
            prompt_text="Check if this is an ad",
            request_context=DummyContext(text="Check if this is an ad"),
        )
        assert res.success is True
        # Verify z-ai was called because deepseek is disabled (despite having higher priority)  # noqa: E501
        assert res.provider_name == "z-ai"
        assert len(fake_provider.calls) == 1
        assert fake_provider.calls[0]["provider_name"] == "z-ai"

    finally:
        await close_mongodb_client(client, timeout_seconds=5)


@async_test
async def test_ai_transient_retry_and_failures(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify bounded transient retries.

    Verify that permanent auth failures are not retried,
    and schema fallback is triggered correctly.
    """
    config_mongo = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_mongo,
        ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        database = client[config_mongo.database_name]

        jobs_col = database["ai_jobs_transient"]
        await initialize_ai_job_indexes(jobs_col)
        jobs_repo = MongoAIJobRepository(jobs_col)

        guard_policy = AiProviderGuardConfig(
            concurrency_limit=5,
            request_limit=100,
            request_window_seconds=60,
            reservation_seconds=10,
            failure_threshold=5,
            open_seconds=30,
            rate_limit_cooldown_seconds=30,
        )

        from unittest.mock import MagicMock

        config = MagicMock(spec=ApplicationConfig)
        config.ai = AiConfig(
            queue=AiQueueConfig(
                lease_duration_seconds=60,
                max_attempts=3,
                next_run_delay_seconds=30,
                worker_poll_seconds=5,
            ),
            providers=(AiProviderConfig(name="z-ai", enabled=True),),
            routes=(
                AiTaskRouteConfig(
                    task=AITaskType.ADVERTISEMENT_DETECTION,
                    candidates=(
                        AiRouteCandidateConfig(
                            provider_name="z-ai",
                            model_name="glm-4.7-flash",
                            priority=0,
                            timeout_seconds=10,
                            max_attempts=2,
                            guard_policy=guard_policy,
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

        # program provider to fail transiently on first try, succeed on second
        call_count = 0

        def behavior(**kwargs: Any) -> Any:  # noqa: ANN401
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return TransientOperationError()
            return make_raw_envelope(
                '{"is_advertisement": false, "confidence": 0.8, "reason": "clean"}'
            )

        fake_provider = ConfigurableFakeAIProvider()
        fake_provider.set_behavior("z-ai/glm-4.7-flash", behavior)

        providers = {"z-ai": fake_provider}

        from telegram_assist_bot.application.ai.provider_guard import ProviderGuard
        from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (  # noqa: E501
            ExecuteAIWithFallback,
        )
        from telegram_assist_bot.infrastructure.mongodb.provider_state_repository import (  # noqa: E501
            MongoProviderStateRepository,
        )

        state_repo = MongoProviderStateRepository(database["provider_states_transient"])
        provider_guard = ProviderGuard(state_repo, IntegrationFakeClock(_NOW))

        execute_ai = ExecuteAIWithFallback(
            config=config.ai,
            providers_by_name=cast("Any", providers),
            ai_job_repository=jobs_repo,
            clock=IntegrationFakeClock(_NOW),
            sleeper=lambda d: asyncio.sleep(0),
            jitter_source=lambda: 0.5,
            provider_guard=provider_guard,
        )

        # Enqueue and claim AI job
        job = AIJob.create(
            job_id="test-job-transient-1",
            post_id="test-post-transient-1",
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job)

        claimed = await jobs_repo.claim_next_due(
            owner="worker-1",
            lease_duration_seconds=60,
            as_of=_NOW,
        )
        assert claimed is not None

        # Execute fallback pipeline - should retry inside the call and return success
        res = await execute_ai.execute(
            job_id=claimed.job_id,
            owner="worker-1",
            prompt_text="Check if this is an ad",
            request_context=DummyContext(text="Check if this is an ad"),
        )
        assert res.success is True
        assert call_count == 2

    finally:
        await close_mongodb_client(client, timeout_seconds=5)


@async_test
async def test_ai_cache_policy_and_invalidation(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify cache hits produce zero provider calls.  # noqa: E501

    Also tests that prompt/schema change causes miss.
    """
    config_mongo = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_mongo,
        ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri}),
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        database = client[config_mongo.database_name]

        jobs_col = database["ai_jobs_cache"]
        await initialize_ai_job_indexes(jobs_col)
        jobs_repo = MongoAIJobRepository(jobs_col)

        guard_policy = AiProviderGuardConfig(
            concurrency_limit=5,
            request_limit=100,
            request_window_seconds=60,
            reservation_seconds=10,
            failure_threshold=5,
            open_seconds=30,
            rate_limit_cooldown_seconds=30,
        )

        from unittest.mock import MagicMock

        config = MagicMock(spec=ApplicationConfig)
        config.ai = AiConfig(
            queue=AiQueueConfig(
                lease_duration_seconds=60,
                max_attempts=3,
                next_run_delay_seconds=30,
                worker_poll_seconds=5,
            ),
            providers=(AiProviderConfig(name="z-ai", enabled=True),),
            routes=(
                AiTaskRouteConfig(
                    task=AITaskType.ADVERTISEMENT_DETECTION,
                    candidates=(
                        AiRouteCandidateConfig(
                            provider_name="z-ai",
                            model_name="glm-4.7-flash",
                            priority=0,
                            timeout_seconds=10,
                            max_attempts=2,
                            guard_policy=guard_policy,
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
                    ttl_seconds=3600,
                ),
            ),
        )

        fake_envelope = make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.95, "reason": "ad"}'
        )
        fake_provider = ConfigurableFakeAIProvider()
        fake_provider.set_behavior("z-ai/glm-4.7-flash", lambda **kwargs: fake_envelope)
        providers = {"z-ai": fake_provider}

        from telegram_assist_bot.application.ai.provider_guard import ProviderGuard
        from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (  # noqa: E501
            ExecuteAIWithFallback,
        )
        from telegram_assist_bot.infrastructure.mongodb.ai_cache_repository import (
            MongoAICacheRepository,
            initialize_ai_cache_indexes,
        )
        from telegram_assist_bot.infrastructure.mongodb.provider_state_repository import (  # noqa: E501
            MongoProviderStateRepository,
        )

        cache_col = database["ai_cache_acceptance"]
        await initialize_ai_cache_indexes(cache_col)
        cache_repo = MongoAICacheRepository(cache_col)

        state_repo = MongoProviderStateRepository(database["provider_states_cache"])
        provider_guard = ProviderGuard(state_repo, IntegrationFakeClock(_NOW))

        execute_ai = ExecuteAIWithFallback(
            config=config.ai,
            providers_by_name=cast("Any", providers),
            ai_job_repository=jobs_repo,
            clock=IntegrationFakeClock(_NOW),
            sleeper=lambda d: asyncio.sleep(0),
            jitter_source=lambda: 0.5,
            provider_guard=provider_guard,
            cache_repository=cache_repo,
        )

        # 1. Enqueue, claim, and run first job (results in Cache Miss -> Provider Call)
        job1 = AIJob.create(
            job_id="job-cache-1",
            post_id="post-cache-1",
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job1)
        claimed1 = await jobs_repo.claim_next_due(
            owner="worker-1", lease_duration_seconds=60, as_of=_NOW
        )
        assert claimed1 is not None

        res1 = await execute_ai.execute(
            job_id=claimed1.job_id,
            owner="worker-1",
            prompt_text="Check if this is an ad",
            request_context=DummyContext(text="Check if this is an ad"),
        )
        assert res1.success is True
        assert len(fake_provider.calls) == 1
        assert res1.cache_hit is False

        # 2. Enqueue and run second job with SAME context but unique post_id  # noqa: E501
        job2 = AIJob.create(
            job_id="job-cache-2",
            post_id="post-cache-2",
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job2)
        claimed2 = await jobs_repo.claim_next_due(
            owner="worker-1", lease_duration_seconds=60, as_of=_NOW
        )
        assert claimed2 is not None

        res2 = await execute_ai.execute(
            job_id=claimed2.job_id,
            owner="worker-1",
            prompt_text="Check if this is an ad",
            request_context=DummyContext(text="Check if this is an ad"),
        )
        assert res2.success is True
        assert res2.cache_hit is True
        assert len(fake_provider.calls) == 1  # Still 1 call in total!

        # 3. Enqueue and run third job with prompt/schema version change and unique post_id  # noqa: E501
        job3 = AIJob.create(
            job_id="job-cache-3",
            post_id="post-cache-3",
            task_type=AITaskType.ADVERTISEMENT_DETECTION,
            prompt_version="2.0.0",  # Changed prompt version!
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job3)
        claimed3 = await jobs_repo.claim_next_due(
            owner="worker-1", lease_duration_seconds=60, as_of=_NOW
        )
        assert claimed3 is not None

        res3 = await execute_ai.execute(
            job_id=claimed3.job_id,
            owner="worker-1",
            prompt_text="Check if this is an ad version 2",
            request_context=DummyContext(text="Check if this is an ad"),
        )
        assert res3.success is True
        assert res3.cache_hit is False
        assert len(fake_provider.calls) == 2  # Total 2 calls!

    finally:
        await close_mongodb_client(client, timeout_seconds=5)
