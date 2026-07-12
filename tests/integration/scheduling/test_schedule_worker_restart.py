"""Prove expired claims recover from MongoDB after a worker restart."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.domain import schedule_identity
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_repository import (  # noqa: E501
    MongoScheduleRepository,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_expired_claim_is_recovered_by_new_worker(
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
            await repository.reserve(
                job_id=schedule_identity("post", -1),
                post_id="post",
                destination_id=-1,
                now=now - timedelta(seconds=1),
                interval=timedelta(seconds=1),
            )
            first = await repository.claim_due(
                owner="crashed", now=now, lease_until=now + timedelta(seconds=1)
            )
            assert first is not None
            assert (
                await repository.claim_due(
                    owner="early", now=now, lease_until=now + timedelta(seconds=2)
                )
                is None
            )
            recovered = await repository.claim_due(
                owner="restart",
                now=now + timedelta(seconds=2),
                lease_until=now + timedelta(seconds=30),
            )
            assert recovered is not None
            assert recovered.job_id == first.job_id
            assert recovered.claim_owner == "restart"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_owned_claim_can_be_deferred_and_terminally_failed(
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
            for index, category in enumerate(("permanent_failed", "ambiguous")):
                reserved = await repository.reserve(
                    job_id=schedule_identity(f"failure-{index}", -1),
                    post_id=f"failure-{index}",
                    destination_id=-1,
                    now=now - timedelta(seconds=1),
                    interval=timedelta(seconds=1),
                )
                claim_time = now + timedelta(seconds=index)
                claimed = await repository.claim_due(
                    owner="worker",
                    now=claim_time,
                    lease_until=claim_time + timedelta(seconds=30),
                )
                assert claimed is not None
                if index == 0:
                    assert await repository.defer(
                        claimed.job_id,
                        owner="worker",
                        next_attempt_at=now + timedelta(seconds=1),
                        category="transient",
                    )
                    reclaimed = await repository.claim_due(
                        owner="worker",
                        now=now + timedelta(seconds=1),
                        lease_until=now + timedelta(seconds=30),
                    )
                    assert reclaimed is not None
                    claimed = reclaimed
                assert await repository.fail(
                    claimed.job_id, owner="worker", category=category
                )
                document = await schedules.find_one({"_id": reserved.job.job_id})
                assert document is not None
                expected = (
                    "OutcomeUnknown" if category == "ambiguous" else "PermanentFailed"
                )
                assert document["status"] == expected
        finally:
            await client.close()

    asyncio.run(scenario())
