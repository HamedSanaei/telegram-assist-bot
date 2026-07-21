"""Integration tests for concurrent AI workers claiming from the queue."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.domain.ai_job import AIJob
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
async def test_concurrent_worker_claims_are_exclusive(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that multiple workers claiming concurrently never get the same job."""
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

        jobs_col = database["ai_jobs_concurrency"]
        await initialize_ai_job_indexes(jobs_col)

        jobs_repo = MongoAIJobRepository(jobs_col)

        # 1. Enqueue 5 jobs
        for i in range(5):
            job = AIJob.create(
                job_id=f"concurrent-job-{i}",
                post_id=f"post-{i}",
                task_type="advertisement_detection",
                prompt_version="1.0.0",
                schema_version="1",
                priority=0,
                max_attempts=3,
                created_at=_NOW,
                next_run_at=_NOW,
            )
            await jobs_repo.enqueue(job)

        # 2. Spawn 10 concurrent claim tasks
        async def claim_task(worker_id: str) -> str | None:
            claimed = await jobs_repo.claim_next_due(
                owner=worker_id,
                lease_duration_seconds=60,
                as_of=_NOW,
            )
            return claimed.job_id if claimed else None

        tasks = [claim_task(f"worker-{w}") for w in range(10)]
        results = await asyncio.gather(*tasks)

        # 3. Filter out None (failed claims)
        successful_claims = [r for r in results if r is not None]

        # 4. Verify uniqueness of claimed jobs: no two workers claimed the same job!
        assert len(successful_claims) == len(set(successful_claims))
        # Total claimed must not exceed 5 (total enqueued)
        assert len(successful_claims) <= 5

    finally:
        await close_mongodb_client(client, timeout_seconds=5)
