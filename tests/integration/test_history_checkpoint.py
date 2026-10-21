from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta

import pytest

from telegram_assist_bot.application.ports.history_checkpoint import HistoryCheckpoint
from telegram_assist_bot.infrastructure.persistence.mongodb.history_checkpoint import (
    MongoHistoryCheckpointRepository,
)
from tests.integration.test_crawl_today_text_posts import (
    _NOW,
    Gateway,
    MongoTestSettings,
    create_crawler,
    item,
    resources,
    run,
)
from tests.unit.application.test_history_recovery import ResumableGateway

pytestmark = pytest.mark.integration


def test_source_checkpoint_insert_and_revision_are_single_winner(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            database = owned.client[mongodb_test_settings.database_name]
            repository = MongoHistoryCheckpointRepository(
                database["history_checkpoints"], database["history_failures"]
            )
            checkpoint = HistoryCheckpoint(-1001, _NOW - timedelta(hours=8), _NOW)
            inserts = await asyncio.gather(
                *(repository.save(checkpoint, expected_revision=None) for _ in range(8))
            )
            assert sum(inserts) == 1
            advances = await asyncio.gather(
                *(
                    repository.save(
                        replace(checkpoint, revision=1, offset_message_id=offset),
                        expected_revision=0,
                    )
                    for offset in range(1, 9)
                )
            )
            assert sum(advances) == 1
            persisted = await repository.get(-1001)
            assert persisted is not None
            assert persisted.revision == 1
            assert not await repository.save(
                replace(checkpoint, revision=2), expected_revision=0
            )
            assert await repository.get(-1001) == persisted

    run(scenario())


def test_checkpoint_restart_resumes_completed_pages_and_retains_failure_acknowledgments(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            database = owned.client[mongodb_test_settings.database_name]

            def repository() -> MongoHistoryCheckpointRepository:
                return MongoHistoryCheckpointRepository(
                    database["history_checkpoints"], database["history_failures"]
                )

            source = ResumableGateway(
                (),
                items=tuple(
                    item(identity, _NOW - timedelta(hours=1))
                    for identity in (4, 3, 2, 1)
                ),
            )
            base = create_crawler(Gateway(()), owned.repository)
            first = await replace(
                base, gateway=source, checkpoints=repository(), page_size=2, max_pages=1
            ).execute(-1001, correlation_id="first")
            assert first.created == 2
            assert not first.completed
            await repository().record_terminal_failure(
                -1001, 2, category="permanent", recorded_at=_NOW
            )
            resumed = await replace(
                base, gateway=source, checkpoints=repository(), page_size=2, max_pages=3
            ).execute(-1001, correlation_id="restart")
            assert resumed.created == 1
            assert resumed.already_existing == 0
            assert resumed.failed == 1
            assert resumed.completed
            assert await owned.collection.count_documents({}) == 3
            assert await repository().is_terminal_failure(-1001, 2)
            checkpoint = await repository().get(-1001)
            assert checkpoint is not None
            assert checkpoint.completed
            unchanged = await replace(
                base, gateway=source, checkpoints=repository(), page_size=2
            ).execute(-1001, correlation_id="unchanged")
            assert unchanged.page_requests == 1
            assert unchanged.completed

    run(scenario())
