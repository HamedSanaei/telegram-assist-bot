"""Prove two workers cannot execute one schedule concurrently."""

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


def test_two_workers_produce_one_effective_publication(
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
            calls = 0

            async def build(post_id: str, destination_id: int) -> PublishRequest:
                return request(post_id=post_id, destination_id=destination_id)

            async def publish(_value: PublishRequest) -> PublishResult:
                nonlocal calls
                calls += 1
                await asyncio.sleep(0)
                return PublishResult(PublishStatus.SUCCEEDED)

            workers = [
                RunDuePublication(
                    repository,
                    owner=f"worker-{index}",
                    clock=lambda: now,
                    lease_seconds=30,
                    max_attempts=3,
                    retry_delay_seconds=1,
                    build_request=build,
                    publish=publish,
                )
                for index in range(2)
            ]
            results = await asyncio.gather(
                *(worker.execute_once() for worker in workers)
            )
            assert sorted(results) == sorted(
                [RunDueStatus.COMPLETED, RunDueStatus.IDLE]
            )
            assert calls == 1
        finally:
            await client.close()

    asyncio.run(scenario())
