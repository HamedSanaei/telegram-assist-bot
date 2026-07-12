"""Verify preserve/recompact and cancel/claim races on real MongoDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pymongo import AsyncMongoClient

from telegram_assist_bot.domain import (
    CancellationPolicy,
    CancellationResult,
    schedule_identity,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_repository import (  # noqa: E501
    MongoScheduleRepository,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


@pytest.mark.parametrize("policy", list(CancellationPolicy))
def test_cancel_policy_is_destination_scoped_and_restart_safe(
    mongodb_test_settings: MongoTestSettings,
    policy: CancellationPolicy,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            schedules, queues = (
                database["scheduled_publications"],
                database["schedule_queues"],
            )
            await initialize_publication_indexes(
                database["publications"], schedules, queues
            )
            repository = MongoScheduleRepository(schedules, queues)
            now, interval = datetime(2026, 7, 12, tzinfo=UTC), timedelta(seconds=300)
            jobs = [
                await repository.reserve(
                    job_id=schedule_identity(f"post-{index}", -1),
                    post_id=f"post-{index}",
                    destination_id=-1,
                    now=now,
                    interval=interval,
                )
                for index in range(3)
            ]
            other = await repository.reserve(
                job_id=schedule_identity("other", -2),
                post_id="other",
                destination_id=-2,
                now=now,
                interval=interval,
            )
            original = [item.job.due_at for item in jobs]
            result = await repository.cancel(
                job_id=jobs[1].job.job_id,
                destination_id=-1,
                expected_version=jobs[1].job.version,
                policy=policy,
                interval=interval,
                actor_id=42,
                now=now,
                correlation_id="safe",
            )
            assert result is CancellationResult.CANCELLED
            documents = [
                await schedules.find_one({"_id": item.job.job_id}) for item in jobs
            ]
            assert documents[1] is not None
            assert documents[1]["status"] == "Cancelled"
            expected_last = (
                original[2] if policy is CancellationPolicy.PRESERVE else original[1]
            )
            assert documents[2] is not None
            assert documents[2]["due_at"] == expected_last
            unchanged = await schedules.find_one({"_id": other.job.job_id})
            assert unchanged is not None
            assert unchanged["due_at"] == other.job.due_at
            assert (
                await repository.claim_due(
                    owner="restart",
                    now=original[1] + interval,
                    lease_until=original[1] + interval * 2,
                )
                is not None
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_cancel_claim_race_has_one_consistent_winner(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            schedules, queues = (
                database["scheduled_publications"],
                database["schedule_queues"],
            )
            await initialize_publication_indexes(
                database["publications"], schedules, queues
            )
            repository = MongoScheduleRepository(schedules, queues)
            now = datetime(2026, 7, 12, tzinfo=UTC)
            reserved = await repository.reserve(
                job_id=schedule_identity("race", -1),
                post_id="race",
                destination_id=-1,
                now=now - timedelta(seconds=1),
                interval=timedelta(seconds=1),
            )
            cancellation, claim = await asyncio.gather(
                repository.cancel(
                    job_id=reserved.job.job_id,
                    destination_id=-1,
                    expected_version=0,
                    policy=CancellationPolicy.PRESERVE,
                    interval=timedelta(seconds=1),
                    actor_id=42,
                    now=now,
                    correlation_id="safe",
                ),
                repository.claim_due(
                    owner="worker", now=now, lease_until=now + timedelta(seconds=30)
                ),
            )
            assert (cancellation is CancellationResult.CANCELLED) != (claim is not None)
        finally:
            await client.close()

    asyncio.run(scenario())
