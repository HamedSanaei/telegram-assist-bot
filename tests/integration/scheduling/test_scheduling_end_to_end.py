"""Exercise reserve, claim, idempotent publish result, and completion end to end."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient
from tests.unit.application.publication.test_publish_text_immediately import request

from telegram_assist_bot.application.publication import PublishResult, PublishStatus
from telegram_assist_bot.application.scheduling import RunDuePublication, RunDueStatus
from telegram_assist_bot.domain import schedule_identity
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_repository import (  # noqa: E501
    MongoScheduleRepository,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

    from telegram_assist_bot.application.publication import PublishRequest


def test_two_destination_jobs_complete_without_process_queue_state(
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
            for destination in (-1001, -1002):
                await repository.reserve(
                    job_id=schedule_identity(f"post-{destination}", destination),
                    post_id=f"post-{destination}",
                    destination_id=destination,
                    now=now - timedelta(seconds=1),
                    interval=timedelta(seconds=1),
                )
            calls: list[tuple[str, int]] = []

            async def build(post_id: str, destination_id: int) -> PublishRequest:
                return request(post_id=post_id, destination_id=destination_id)

            async def publish(value: PublishRequest) -> PublishResult:
                calls.append((value.post_id, value.destination_id))
                return PublishResult(PublishStatus.SUCCEEDED)

            worker = RunDuePublication(
                repository,
                owner="worker",
                clock=lambda: now,
                lease_seconds=30,
                max_attempts=3,
                retry_delay_seconds=1,
                build_request=build,
                publish=publish,
            )
            assert await worker.execute_once() is RunDueStatus.COMPLETED
            assert await worker.execute_once() is RunDueStatus.COMPLETED
            assert await worker.execute_once() is RunDueStatus.IDLE
            assert len(calls) == 2
            assert await schedules.count_documents({"status": "Completed"}) == 2
        finally:
            await client.close()

    asyncio.run(scenario())
