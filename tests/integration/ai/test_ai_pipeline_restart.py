"""Integration tests for AI pipeline restart safety and lease recovery."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

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


@async_test
async def test_ai_pipeline_lease_recovery_after_restart(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that expired leases are safely reclaimed by another worker after a restart."""  # noqa: E501
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

        jobs_col = database["ai_jobs_restart"]
        await initialize_ai_job_indexes(jobs_col)

        jobs_repo = MongoAIJobRepository(jobs_col)

        # 1. Enqueue job
        job = AIJob.create(
            job_id="test-job-restart-1",
            post_id="test-post-restart-1",
            task_type="advertisement_detection",
            prompt_version="1.0.0",
            schema_version="1",
            priority=0,
            max_attempts=3,
            created_at=_NOW,
            next_run_at=_NOW,
        )
        await jobs_repo.enqueue(job)

        # 2. Worker 1 claims job with 10-second lease
        claimed_w1 = await jobs_repo.claim_next_due(
            owner="worker-1",
            lease_duration_seconds=10,
            as_of=_NOW,
        )
        assert claimed_w1 is not None
        assert claimed_w1.lease_owner == "worker-1"
        assert claimed_w1.status is AIJobStatus.PROCESSING

        # 3. Try to claim with Worker 2 immediately - should return None (still leased)
        claimed_w2_early = await jobs_repo.claim_next_due(
            owner="worker-2",
            lease_duration_seconds=10,
            as_of=_NOW,
        )
        assert claimed_w2_early is None

        # 4. Progress time beyond lease expiry (e.g. +11 seconds)
        future_time = _NOW + timedelta(seconds=11)

        # 5. Worker 2 claims job - lease has expired, so Worker 2 should reclaim it!
        claimed_w2_late = await jobs_repo.claim_next_due(
            owner="worker-2",
            lease_duration_seconds=10,
            as_of=future_time,
        )
        assert claimed_w2_late is not None
        assert claimed_w2_late.job_id == "test-job-restart-1"
        assert claimed_w2_late.lease_owner == "worker-2"
        assert claimed_w2_late.attempts == 2

    finally:
        await close_mongodb_client(client, timeout_seconds=5)
