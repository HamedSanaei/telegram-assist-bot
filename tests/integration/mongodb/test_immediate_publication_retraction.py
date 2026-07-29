"""MongoDB acceptance for durable immediate-publication retraction."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.ports import (
    PublicationPreparationOutcome,
    PublicationRetractionRequestOutcome,
)
from telegram_assist_bot.domain import (
    PublicationRetractionState,
    PublicationState,
    PublishedMessage,
    publication_identity,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPublicationRepository,
    MongoScheduleRepository,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)


async def successful_receipt(
    repository: MongoPublicationRepository,
    *,
    post_id: str = "post",
    destination_id: int = -1001,
) -> str:
    """Persist one canonical successful publication receipt."""
    identity = publication_identity(post_id, destination_id, "immediate")
    claimed = await repository.claim(
        publication_id=identity,
        post_id=post_id,
        destination_id=destination_id,
        owner="publisher",
        now=NOW,
        lease_until=NOW + timedelta(seconds=30),
        max_attempts=3,
        correlation_id="corr",
    )
    assert claimed.publication.state is PublicationState.CLAIMED
    await repository.complete(
        identity,
        owner="publisher",
        result=PublishedMessage((51, 52), NOW + timedelta(seconds=1)),
    )
    return identity


def test_request_claim_restart_and_competing_workers(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            publications = database["publications"]
            schedules = database["scheduled_publications"]
            queues = database["schedule_queues"]
            await initialize_publication_indexes(publications, schedules, queues)
            repository = MongoPublicationRepository(publications)
            identity = await successful_receipt(repository)
            requests = await asyncio.gather(
                *(
                    repository.request_retraction(
                        identity, now=NOW, selection_version=2
                    )
                    for _ in range(5)
                )
            )
            assert requests.count(PublicationRetractionRequestOutcome.REQUESTED) == 1
            assert all(
                item
                in {
                    PublicationRetractionRequestOutcome.REQUESTED,
                    PublicationRetractionRequestOutcome.ALREADY_REQUESTED,
                }
                for item in requests
            )
            claims = await asyncio.gather(
                repository.claim_retraction(
                    owner="worker-one",
                    now=NOW,
                    lease_until=NOW + timedelta(seconds=10),
                    max_attempts=3,
                ),
                repository.claim_retraction(
                    owner="worker-two",
                    now=NOW,
                    lease_until=NOW + timedelta(seconds=10),
                    max_attempts=3,
                ),
            )
            assert sum(item is not None for item in claims) == 1
            claimed = next(item for item in claims if item is not None)
            assert claimed.message_ids == (51, 52)
            assert claimed.destination_id == -1001

            restarted = MongoPublicationRepository(publications)
            reclaimed = await restarted.claim_retraction(
                owner="worker-after-restart",
                now=NOW + timedelta(seconds=11),
                lease_until=NOW + timedelta(seconds=21),
                max_attempts=3,
            )
            assert reclaimed is not None
            completed = await restarted.complete_retraction(
                identity,
                owner="worker-after-restart",
                at=NOW + timedelta(seconds=12),
            )
            assert completed.retraction_state is PublicationRetractionState.SUCCEEDED
            assert completed.retraction_selection_version == 2
            assert (
                await restarted.claim_retraction(
                    owner="late-worker",
                    now=NOW + timedelta(days=1),
                    lease_until=NOW + timedelta(days=1, seconds=10),
                    max_attempts=3,
                )
                is None
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_retraction_gates_and_republication_cycle(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            publications = database["publications"]
            schedules = database["scheduled_publications"]
            queues = database["schedule_queues"]
            await initialize_publication_indexes(publications, schedules, queues)
            repository = MongoPublicationRepository(publications)
            schedule_repository = MongoScheduleRepository(schedules, queues)
            identity = publication_identity("post", -1001, "immediate")
            assert (
                await repository.request_retraction(
                    identity, now=NOW, selection_version=1
                )
                is PublicationRetractionRequestOutcome.NOT_PUBLISHED
            )
            await schedule_repository.reserve_immediate(
                job_id=identity,
                post_id="post",
                destination_id=-1001,
                now=NOW,
            )
            schedule_claim = await schedule_repository.claim_due(
                owner="schedule-worker",
                now=NOW,
                lease_until=NOW + timedelta(seconds=30),
                action="immediate",
            )
            assert schedule_claim is not None
            assert await schedule_repository.complete(
                identity, owner="schedule-worker", at=NOW + timedelta(seconds=1)
            )
            await successful_receipt(repository)
            assert (
                await repository.prepare_republication(identity, now=NOW)
                is PublicationPreparationOutcome.BLOCKED
            )
            assert (
                await repository.request_retraction(
                    identity, now=NOW, selection_version=2
                )
                is PublicationRetractionRequestOutcome.REQUESTED
            )
            claimed = await repository.claim_retraction(
                owner="worker",
                now=NOW,
                lease_until=NOW + timedelta(seconds=30),
                max_attempts=3,
            )
            assert claimed is not None
            await repository.complete_retraction(
                identity, owner="worker", at=NOW + timedelta(seconds=1)
            )
            assert (
                await repository.prepare_republication(
                    identity, now=NOW + timedelta(seconds=2)
                )
                is PublicationPreparationOutcome.READY
            )
            reopened = await schedule_repository.reserve_immediate(
                job_id=identity,
                post_id="post",
                destination_id=-1001,
                now=NOW + timedelta(seconds=2),
                reopen=True,
            )
            assert reopened.created
            assert reopened.job.due_at == NOW + timedelta(seconds=2)
            stored = await publications.find_one({"_id": identity})
            assert stored is not None
            stored_document = cast("dict[str, Any]", stored)
            assert stored_document["state"] == PublicationState.PENDING.value
            assert stored_document["message_ids"] == []
            assert stored_document["publication_history"][0]["message_ids"] == [51, 52]
        finally:
            await client.close()

    asyncio.run(scenario())
