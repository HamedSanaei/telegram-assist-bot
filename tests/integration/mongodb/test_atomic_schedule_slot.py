"""Prove unique ordered per-destination slots on real test MongoDB."""

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

    from telegram_assist_bot.application.ports import ScheduleReservation


def test_concurrent_slots_are_unique_ordered_and_destination_independent(
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
            now, interval = datetime(2026, 7, 12, tzinfo=UTC), timedelta(seconds=300)

            async def reserve(index: int, destination: int) -> ScheduleReservation:
                return await repository.reserve(
                    job_id=schedule_identity(f"post-{index}", destination),
                    post_id=f"post-{index}",
                    destination_id=destination,
                    now=now,
                    interval=interval,
                )

            first_queue = await asyncio.gather(*(reserve(i, -1001) for i in range(10)))
            second_queue = await asyncio.gather(*(reserve(i, -1002) for i in range(3)))
            due = sorted(item.job.due_at for item in first_queue)
            assert due == [now + interval * value for value in range(1, 11)]
            assert sorted(item.job.due_at for item in second_queue) == [
                now + interval * value for value in range(1, 4)
            ]
            repeated = await reserve(0, -1001)
            assert not repeated.created
            assert await schedules.count_documents({"destination_id": -1001}) == 10
            duplicate_results = await asyncio.gather(
                *(reserve(99, -1001) for _ in range(12))
            )
            assert sum(item.created for item in duplicate_results) == 1
            queue = await queues.find_one({"_id": -1001})
            assert queue is not None
            slots = queue["slots"]
            assert isinstance(slots, list)
            assert len(slots) == 11
        finally:
            await client.close()

    asyncio.run(scenario())
