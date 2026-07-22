"""Integration tests for AI Job lease parameters.

Includes expiration, reclaim, and restart durability.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ai.claim_ai_job import ClaimAIJob
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.domain.ai_job import AIJobStatus
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
    ResolvedSecrets,
    SecretReference,
)

if TYPE_CHECKING:
    from tests.integration.test_album_finalization_repository import MongoTestSettings

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


class StubClock:
    """A controllable clock for lease expiry and recovery verification."""

    def __init__(self, initial_time: datetime) -> None:
        self._now = initial_time.astimezone(UTC)

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, duration: timedelta) -> None:
        self._now += duration


def test_ai_job_lease_expiry_and_reclaim(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that a job whose lease expires can be reclaimed by another worker."""

    async def scenario() -> None:
        config = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            config, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            db = client[config.database_name]
            collection = db["ai_jobs"]
            await initialize_ai_job_indexes(collection)

            base_time = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
            clock = StubClock(base_time)
            repo = MongoAIJobRepository(collection)
            enqueue_uc = EnqueueAIJob(repo, clock)
            claim_uc = ClaimAIJob(repo, clock)

            # Enqueue a job
            await enqueue_uc.execute(
                "post-1", "scoring", "1.0", "1", priority=20, job_id="job-1"
            )

            # Claim by Worker 1 for 10 seconds lease
            claimed_1 = await claim_uc.execute(
                owner="worker-1", lease_duration_seconds=10
            )
            assert claimed_1 is not None
            assert claimed_1.lease_owner == "worker-1"
            assert claimed_1.status == AIJobStatus.PROCESSING

            # Claiming again immediately by Worker 2 yields None (lease is still active)
            claimed_2_immediate = await claim_uc.execute(
                owner="worker-2", lease_duration_seconds=10
            )
            assert claimed_2_immediate is None

            # Advance clock by 11 seconds (lease expired)
            clock.advance(timedelta(seconds=11))

            # Claim by Worker 2 should succeed (reclaimed because of lease expiry)
            claimed_2 = await claim_uc.execute(
                owner="worker-2", lease_duration_seconds=10
            )
            assert claimed_2 is not None
            assert claimed_2.lease_owner == "worker-2"
            assert claimed_2.attempts == 2  # Incremented attempt counter

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_ai_job_restart_recovery_and_durability(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that jobs are durable and survive repository client restarts."""

    async def scenario() -> None:
        config = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )

        # Connect and enqueue job
        client = create_mongodb_client(
            config, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            db = client[config.database_name]
            collection = db["ai_jobs"]
            await initialize_ai_job_indexes(collection)

            base_time = datetime(2026, 7, 17, 12, 0, tzinfo=UTC)
            clock = StubClock(base_time)
            repo1 = MongoAIJobRepository(collection)

            await EnqueueAIJob(repo1, clock).execute(
                "post-1", "scoring", "1.0", "1", priority=20, job_id="job-1"
            )

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

        # Simulate client restart by creating a completely new connection
        client2 = create_mongodb_client(
            config, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
        )
        try:
            await verify_mongodb_connection(client2, timeout_seconds=5)
            db2 = client2[config.database_name]
            collection2 = db2["ai_jobs"]
            repo2 = MongoAIJobRepository(collection2)
            clock = StubClock(base_time)

            # Confirm the job is still there and can be claimed
            job = await repo2.get_by_id("job-1")
            assert job is not None
            assert job.status == AIJobStatus.PENDING

            claimed = await ClaimAIJob(repo2, clock).execute(
                owner="worker-1", lease_duration_seconds=60
            )
            assert claimed is not None
            assert claimed.job_id == "job-1"

        finally:
            await close_mongodb_client(client2, timeout_seconds=5)

    asyncio.run(scenario())
