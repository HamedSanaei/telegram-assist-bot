"""Integration tests for AI fallback pipeline and MongoDB compatibility."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from telegram_assist_bot.application.ai.contracts import AITaskType, RawResponseEnvelope
from telegram_assist_bot.application.ai.use_cases.execute_ai_with_fallback import (
    AllProvidersFailedError,
    ExecuteAIWithFallback,
)
from telegram_assist_bot.application.ports import AIProvider
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus
from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
    MongoAIJobRepository,
    initialize_ai_job_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
    create_mongodb_client,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    AiConfig,
    AiProviderConfig,
    AiQueueConfig,
    AiRouteCandidateConfig,
    AiTaskFailureAction,
    AiTaskFailurePolicyConfig,
    AiTaskRouteConfig,
    MongoConfig,
    SecretReference,
)
from telegram_assist_bot.shared.config.loader import ResolvedSecrets
from telegram_assist_bot.shared.errors import TransientOperationError

if TYPE_CHECKING:
    from tests.conftest import MongoTestSettings

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


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
    """Mock clock that progresses time deterministically."""

    def __init__(self, start_time: datetime | None = None) -> None:
        self.current = start_time or datetime(2026, 7, 17, 12, 0, 0, tzinfo=UTC)

    def utc_now(self) -> datetime:
        return self.current


class IntegrationFakeSleeper:
    """Fake sleeper for integration tests."""

    async def __call__(self, delay: float) -> None:
        pass


class IntegrationFakeJitter:
    """Deterministic jitter source."""

    def __call__(self) -> float:
        return 0.5


class FakeAIProvider(AIProvider):
    """Fake AI provider configured to return canned responses or raise errors."""

    def __init__(self, behavior: list[RawResponseEnvelope | Exception]) -> None:
        self.behavior = behavior

    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        if not self.behavior:
            raise RuntimeError("FakeAIProvider has no programmed behavior left.")
        item = self.behavior.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


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
        model_name="test-model",
        provider_name="test-provider",
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )


@async_test
async def test_fallback_pipeline_integration_with_mongodb(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verifies that ExecuteAIWithFallback updates state/telemetry in MongoDB."""
    config_settings = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_settings, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        db = client[config_settings.database_name]
        collection = db["ai_jobs_test_fallback"]
        await initialize_ai_job_indexes(collection)

        # 1. Setup route configuration
        ai_config = AiConfig(
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
                        AiRouteCandidateConfig(
                            provider_name="z-ai",
                            model_name="glm-4.7-flash",
                            priority=1,
                            timeout_seconds=30,
                            max_attempts=1,
                        ),
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

        # Program behaviors: first provider fails, second succeeds
        provider_zai = FakeAIProvider(
            [TransientOperationError(cause=ConnectionError("z-ai network error"))]
        )
        provider_deepseek = FakeAIProvider(
            [
                make_raw_envelope(
                    '{"is_advertisement": true, "confidence": 0.9, "reason": "spam"}'
                )
            ]
        )

        providers = {"z-ai": provider_zai, "deepseek": provider_deepseek}

        # 2. Insert claimed job
        repo = MongoAIJobRepository(collection)
        job = AIJob(
            job_id="job-integration-1",
            post_id="post-integration-abc",
            task_type="advertisement_detection",
            prompt_version="1",
            schema_version="1",
            idempotency_key="post-integration-abc:advertisement_detection:1:1",
            status=AIJobStatus.PROCESSING,
            priority=20,
            attempts=1,
            max_attempts=3,
            next_run_at=datetime.now(UTC),
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            version=1,
        )
        await repo.enqueue(job)

        # Execute fallback pipeline
        clock = IntegrationFakeClock()
        sleeper = IntegrationFakeSleeper()
        jitter = IntegrationFakeJitter()

        orchestrator = ExecuteAIWithFallback(
            config=ai_config,
            providers_by_name=providers,
            ai_job_repository=repo,
            clock=clock,
            sleeper=sleeper,
            jitter_source=jitter,
        )

        result = await orchestrator.execute(
            job_id="job-integration-1",
            owner="worker-1",
            prompt_text="Check spam",
            request_context=DummyContext(text="Click here for money!"),
        )

        assert result is not None
        assert result.payload["is_advertisement"] is True
        assert result.provider_name == "deepseek"

        # 3. Retrieve from MongoDB and assert updated status and telemetry
        updated_job = await repo.get_by_id("job-integration-1")
        assert updated_job is not None
        assert updated_job.status == AIJobStatus.COMPLETED
        assert updated_job.result == result.payload
        assert updated_job.attempted_candidates_count == 2
        assert updated_job.fallback_count == 1
        assert updated_job.retry_count == 0
        assert len(updated_job.attempts_history or []) == 2
        assert updated_job.attempts_history[0]["provider_name"] == "z-ai"
        assert updated_job.attempts_history[0]["success"] is False
        assert updated_job.attempts_history[1]["provider_name"] == "deepseek"
        assert updated_job.attempts_history[1]["success"] is True

    finally:
        await client.close()


@async_test
async def test_fallback_pipeline_all_providers_failed_persists_properly(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verifies that final failure of all providers saves telemetry properly."""
    config_settings = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_settings, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        db = client[config_settings.database_name]
        collection = db["ai_jobs_test_all_fail"]
        await initialize_ai_job_indexes(collection)

        ai_config = AiConfig(
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
                    action=AiTaskFailureAction.CONTINUE,
                ),
            ),
        )

        provider_zai = FakeAIProvider(
            [TransientOperationError(cause=ConnectionError("z-ai network error"))]
        )
        providers = {"z-ai": provider_zai}

        repo = MongoAIJobRepository(collection)
        job = AIJob(
            job_id="job-integration-2",
            post_id="post-integration-abc",
            task_type="advertisement_detection",
            prompt_version="1",
            schema_version="1",
            idempotency_key="post-integration-abc:advertisement_detection:1:1",
            status=AIJobStatus.PROCESSING,
            priority=20,
            attempts=3,  # Last attempt
            max_attempts=3,
            next_run_at=datetime.now(UTC),
            lease_owner="worker-1",
            lease_expires_at=datetime.now(UTC) + timedelta(minutes=1),
            version=1,
        )
        await repo.enqueue(job)

        orchestrator = ExecuteAIWithFallback(
            config=ai_config,
            providers_by_name=providers,
            ai_job_repository=repo,
            clock=IntegrationFakeClock(),
            sleeper=IntegrationFakeSleeper(),
            jitter_source=IntegrationFakeJitter(),
        )

        with pytest.raises(AllProvidersFailedError) as exc_info:
            await orchestrator.execute(
                job_id="job-integration-2",
                owner="worker-1",
                prompt_text="Check spam",
                request_context=DummyContext(text="Click here for money!"),
            )

        assert exc_info.value.action == AiTaskFailureAction.CONTINUE

        # Retrieve and assert final failure state
        updated_job = await repo.get_by_id("job-integration-2")
        assert updated_job is not None
        assert updated_job.status == AIJobStatus.ALL_PROVIDERS_FAILED
        assert updated_job.safe_last_failure_code == "transient"
        assert len(updated_job.attempts_history or []) == 1

    finally:
        await client.close()


@async_test
async def test_legacy_mongodb_ai_job_compatibility(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verifies that legacy docs are deserialized correctly with defaults."""
    config_settings = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_settings, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
    )
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        db = client[config_settings.database_name]
        collection = db["ai_jobs_test_compatibility"]

        # Insert a raw BSON document lacking the new telemetry fields
        legacy_doc = {
            "_id": "legacy-job-789",
            "post_id": "post-999",
            "task_type": "advertisement_detection",
            "prompt_version": "1.0",
            "schema_version": "1",
            "idempotency_key": "post-999:advertisement_detection:1.0:1",
            "status": "Pending",
            "priority": 10,
            "attempts": 0,
            "max_attempts": 3,
            "next_run_at": datetime.now(UTC),
            "next_run_at_rem": 0,
            "lease_owner": None,
            "lease_expires_at": None,
            "lease_expires_at_rem": 0,
            "result": None,
            "last_error": None,
            "created_at": datetime.now(UTC),
            "created_at_rem": 0,
            "updated_at": datetime.now(UTC),
            "updated_at_rem": 0,
            "version": 0,
        }
        await collection.insert_one(legacy_doc)

        # Read using repository and assert defaults
        repo = MongoAIJobRepository(collection)
        job = await repo.get_by_id("legacy-job-789")
        assert job is not None
        assert job.job_id == "legacy-job-789"
        assert job.attempts_history is None
        assert job.attempted_candidates_count is None
        assert job.retry_count is None
        assert job.fallback_count is None
        assert job.safe_last_failure_code is None

        # Verify round-trip: save updated job back to MongoDB and confirm fields exist
        job = replace(
            job,
            retry_count=2,
            fallback_count=1,
            safe_last_failure_code="transient",
        )
        # Advance version for optimistic concurrency update simulation
        job = replace(job, version=job.version + 1)
        await repo.update(job)

        raw_doc = await collection.find_one({"_id": "legacy-job-789"})
        assert raw_doc is not None
        assert raw_doc["retry_count"] == 2
        assert raw_doc["fallback_count"] == 1
        assert raw_doc["safe_last_failure_code"] == "transient"

    finally:
        await client.close()
