"""Integration tests for AI Job Queue.

Includes durable enqueue, priority claim, and concurrency tests.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ai.claim_ai_job import ClaimAIJob
from telegram_assist_bot.application.ai.enqueue_ai_job import EnqueueAIJob
from telegram_assist_bot.application.ports import (
    AIJobConcurrencyConflictError,
    AIJobNotFoundError,
    EnqueueJobOutcome,
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
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)

if TYPE_CHECKING:
    from tests.integration.test_album_finalization_repository import MongoTestSettings

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


class StubClock:
    """A controllable clock for ordering and due time verification."""

    def __init__(self, initial_time: datetime) -> None:
        self._now = initial_time.astimezone(UTC)

    def utc_now(self) -> datetime:
        return self._now

    def advance(self, duration: timedelta) -> None:
        self._now += duration


def test_ai_job_durable_enqueue_and_idempotency(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that enqueue is idempotent.

    Asserts duplicates are rejected and indexes are defined.
    """

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

            # 1. Initialize indexes
            await initialize_ai_job_indexes(collection)

            # Confirm unique index on idempotency_key is created
            cursor = await collection.list_indexes()
            indexes = [idx["name"] for idx in await cursor.to_list(length=10)]
            assert "uq_ai_jobs_idempotency_key_v1" in indexes

            clock = StubClock(datetime(2026, 7, 17, 12, 0, tzinfo=UTC))
            repo = MongoAIJobRepository(collection)
            use_case = EnqueueAIJob(repo, clock)

            # 2. First enqueue -> Created
            res1 = await use_case.execute(
                post_id="post-123",
                task_type="advertisement_detection",
                prompt_version="1.0.0",
                schema_version="1",
                priority=30,
            )
            assert res1.outcome == EnqueueJobOutcome.CREATED
            assert res1.job.status == AIJobStatus.PENDING

            # 3. Duplicate enqueue -> AlreadyExists with existing job returned
            res2 = await use_case.execute(
                post_id="post-123",
                task_type="advertisement_detection",
                prompt_version="1.0.0",
                schema_version="1",
                priority=10,  # Different priority ignored
            )
            assert res2.outcome == EnqueueJobOutcome.ALREADY_EXISTS
            assert res2.job.job_id == res1.job.job_id
            assert res2.job.priority == 30  # Preserved original

            # 4. Raw direct MongoDB insert of duplicate idempotency key fails
            duplicate_job = AIJob.create(
                job_id="job-dup",
                post_id="post-123",
                task_type="advertisement_detection",
                prompt_version="1.0.0",
                schema_version="1",
                priority=10,
            )
            from telegram_assist_bot.infrastructure.mongodb.ai_job_repository import (
                ai_job_to_document,
            )

            with pytest.raises(DuplicateKeyError):
                await collection.insert_one(ai_job_to_document(duplicate_job))

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_ai_job_claim_priority_and_due_time_ordering(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that jobs are claimed based on priority, next_run_at,

    and created_at ordering.
    """

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

            # Enqueue Job A: Priority MEDIUM, created at T
            await enqueue_uc.execute(
                "post-A", "scoring", "1.0", "1", priority=20, job_id="job-A"
            )

            # Advance clock, Enqueue Job B: Priority HIGH, created at T + 10s
            clock.advance(timedelta(seconds=10))
            await enqueue_uc.execute(
                "post-B", "scoring", "1.0", "1", priority=30, job_id="job-B"
            )

            # Advance clock, Enqueue Job C: Priority HIGH, created at T + 20s
            clock.advance(timedelta(seconds=10))
            await enqueue_uc.execute(
                "post-C", "scoring", "1.0", "1", priority=30, job_id="job-C"
            )

            # Claim 1: Should be Job B (Highest priority, oldest created among highest)
            claimed_1 = await claim_uc.execute(
                owner="worker-1", lease_duration_seconds=60
            )
            assert claimed_1 is not None
            assert claimed_1.job_id == "job-B"
            assert claimed_1.lease_owner == "worker-1"

            # Claim 2: Should be Job C (Highest priority left)
            claimed_2 = await claim_uc.execute(
                owner="worker-2", lease_duration_seconds=60
            )
            assert claimed_2 is not None
            assert claimed_2.job_id == "job-C"

            # Claim 3: Should be Job A (Medium priority left)
            claimed_3 = await claim_uc.execute(
                owner="worker-3", lease_duration_seconds=60
            )
            assert claimed_3 is not None
            assert claimed_3.job_id == "job-A"

            # Claim 4: None left
            claimed_4 = await claim_uc.execute(
                owner="worker-1", lease_duration_seconds=60
            )
            assert claimed_4 is None

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_ai_job_update_optimistic_concurrency(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that update checks version and owner/lease state.

    Applies to terminal transitions.
    """

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

            # Enqueue and Claim
            await EnqueueAIJob(repo, clock).execute(
                "post-1", "scoring", "1.0", "1", priority=20, job_id="job-1"
            )

            claimed = await ClaimAIJob(repo, clock).execute(
                owner="worker-1", lease_duration_seconds=60
            )
            assert claimed is not None
            assert claimed.version == 1

            # Attempt to complete with wrong owner -> Domain fails
            with pytest.raises(ValueError, match="Only the lease owner"):
                claimed.complete(
                    owner="worker-2",
                    result={"score": 5},
                    completed_at=clock.utc_now(),
                )

            # Complete successfully with owner
            completed_job = claimed.complete(
                owner="worker-1", result={"score": 8}, completed_at=clock.utc_now()
            )
            assert completed_job.version == 2
            await repo.update(completed_job)

            # Attempt to update completions with stale version (version conflict)
            # stale_completed has version 3 but DB has version 2.
            # If we try to update with a version that doesn't match:
            stale_job_object = AIJob(
                job_id=completed_job.job_id,
                post_id=completed_job.post_id,
                task_type=completed_job.task_type,
                prompt_version=completed_job.prompt_version,
                schema_version=completed_job.schema_version,
                idempotency_key=completed_job.idempotency_key,
                status=completed_job.status,
                priority=completed_job.priority,
                attempts=completed_job.attempts,
                max_attempts=completed_job.max_attempts,
                next_run_at=completed_job.next_run_at,
                result=completed_job.result,
                version=1,  # Stale version in DB is 2
            )
            with pytest.raises(AIJobConcurrencyConflictError):
                await repo.update(stale_job_object)

            # Unknown job id update raises NotFoundError
            unknown_job_object = AIJob(
                job_id="job-unknown",
                post_id=completed_job.post_id,
                task_type=completed_job.task_type,
                prompt_version=completed_job.prompt_version,
                schema_version=completed_job.schema_version,
                idempotency_key="unknown",
                status=completed_job.status,
                priority=completed_job.priority,
                attempts=completed_job.attempts,
                max_attempts=completed_job.max_attempts,
                next_run_at=completed_job.next_run_at,
                version=1,
            )
            with pytest.raises(AIJobNotFoundError):
                await repo.update(unknown_job_object)

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_ai_job_concurrent_claims_isolation(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Verify that multiple concurrent workers claiming jobs

    result in exactly one winner per job.
    """

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

            # Enqueue 5 jobs
            for i in range(5):
                await EnqueueAIJob(repo, clock).execute(
                    f"post-{i}",
                    "scoring",
                    "1.0",
                    "1",
                    priority=20,
                    job_id=f"job-{i}",
                )

            # Multiple claims concurrently
            claim_uc = ClaimAIJob(repo, clock)

            async def worker_claim(worker_id: str) -> AIJob | None:
                # Add a microsecond jitter to simulate real network randomness
                await asyncio.sleep(0.001)
                return await claim_uc.execute(
                    owner=worker_id, lease_duration_seconds=60
                )

            tasks = [worker_claim(f"worker-{i}") for i in range(10)]
            results = await asyncio.gather(*tasks)

            claimed_jobs = [r for r in results if r is not None]

            # Exactly 5 jobs should be claimed
            assert len(claimed_jobs) == 5

            # No job should be claimed twice by different workers
            job_ids = [j.job_id for j in claimed_jobs]
            assert len(set(job_ids)) == 5

        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
