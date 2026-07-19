"""Integration tests for AI pipeline acceptance criteria and end-to-end flow."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
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


class FakeAIProvider:
    def __init__(self, responses: list[RawResponseEnvelope | Exception]) -> None:
        self.responses = responses

    async def execute_attempt(
        self,
        task_type: AITaskType,
        prompt: str,
        request_context: BaseModel,
        provider_name: str,
        model_name: str,
        timeout_seconds: float,
    ) -> RawResponseEnvelope:
        if not self.responses:
            raise RuntimeError("No programmed behavior left")
        res = self.responses.pop(0)
        if isinstance(res, Exception):
            raise res
        return res


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

        # Create config
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

        # Setup provider
        fake_envelope = make_raw_envelope(
            '{"is_advertisement": true, "confidence": 0.95, "reason": "ad"}'
        )
        fake_provider = FakeAIProvider([fake_envelope])
        providers = {"z-ai": fake_provider}

        # Setup guard and execute_ai_with_fallback
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
            providers_by_name=providers,
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
