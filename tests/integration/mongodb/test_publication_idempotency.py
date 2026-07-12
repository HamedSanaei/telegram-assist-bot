"""Prove atomic publication identity and lease recovery on real test MongoDB."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.ports import (
    PublicationClaimOutcome,
    PublicationClaimResult,
)
from telegram_assist_bot.domain import (
    PublicationFailureCategory,
    PublicationState,
    PublishedMessage,
    publication_identity,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_repository import (  # noqa: E501
    MongoPublicationRepository,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_concurrent_claim_has_one_winner_and_terminal_success_is_reused(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            publications, schedules, queues = (
                database["publications"],
                database["scheduled_publications"],
                database["schedule_queues"],
            )
            await initialize_publication_indexes(publications, schedules, queues)
            await initialize_publication_indexes(publications, schedules, queues)
            repository = MongoPublicationRepository(publications)
            now = datetime(2026, 7, 12, tzinfo=UTC)
            identity = publication_identity("post", -1001)

            async def claim(owner: str) -> PublicationClaimResult:
                return await repository.claim(
                    publication_id=identity,
                    post_id="post",
                    destination_id=-1001,
                    owner=owner,
                    now=now,
                    lease_until=now + timedelta(seconds=30),
                    max_attempts=3,
                    correlation_id="safe",
                )

            results = await asyncio.gather(
                *(claim(f"worker-{index}") for index in range(12))
            )
            winners = [
                item
                for item in results
                if item.outcome is PublicationClaimOutcome.CLAIMED
            ]
            assert len(winners) == 1
            owner = winners[0].publication.claim_owner
            assert owner is not None
            completed = await repository.complete(
                identity, owner=owner, result=PublishedMessage((77,), now)
            )
            assert completed.message_ids == (77,)
            again = await claim("later")
            assert again.outcome is PublicationClaimOutcome.TERMINAL
            assert await publications.count_documents({"_id": identity}) == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_expired_nonterminal_lease_is_recoverable(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            publications = database["publications"]
            await initialize_publication_indexes(
                publications,
                database["scheduled_publications"],
                database["schedule_queues"],
            )
            repository = MongoPublicationRepository(publications)
            old = datetime(2026, 7, 12, tzinfo=UTC)
            identity = publication_identity("recover", -1001)
            first = await repository.claim(
                publication_id=identity,
                post_id="recover",
                destination_id=-1001,
                owner="crashed",
                now=old,
                lease_until=old + timedelta(seconds=1),
                max_attempts=3,
                correlation_id="safe",
            )
            second = await repository.claim(
                publication_id=identity,
                post_id="recover",
                destination_id=-1001,
                owner="restart",
                now=old + timedelta(seconds=2),
                lease_until=old + timedelta(seconds=30),
                max_attempts=3,
                correlation_id="safe",
            )
            assert first.outcome is PublicationClaimOutcome.CLAIMED
            assert second.outcome is PublicationClaimOutcome.CLAIMED
            assert second.publication.claim_owner == "restart"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_retry_permanent_and_unknown_failure_states_are_persisted(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            publications = database["publications"]
            await initialize_publication_indexes(
                publications,
                database["scheduled_publications"],
                database["schedule_queues"],
            )
            repository = MongoPublicationRepository(publications)
            now = datetime(2026, 7, 12, tzinfo=UTC)
            expected = (
                (
                    PublicationFailureCategory.TRANSIENT,
                    now + timedelta(seconds=1),
                    False,
                    PublicationState.WAITING_FOR_RETRY,
                ),
                (
                    PublicationFailureCategory.PERMISSION,
                    None,
                    False,
                    PublicationState.PERMANENT_FAILED,
                ),
                (
                    PublicationFailureCategory.AMBIGUOUS,
                    None,
                    True,
                    PublicationState.OUTCOME_UNKNOWN,
                ),
            )
            for index, (category, next_at, unknown, state) in enumerate(expected):
                identity = publication_identity(f"failure-{index}", -1)
                await repository.claim(
                    publication_id=identity,
                    post_id=f"failure-{index}",
                    destination_id=-1,
                    owner="worker",
                    now=now,
                    lease_until=now + timedelta(seconds=30),
                    max_attempts=3,
                    correlation_id="safe",
                )
                failed = await repository.fail(
                    identity,
                    owner="worker",
                    category=category,
                    now=now,
                    next_attempt_at=next_at,
                    outcome_unknown=unknown,
                )
                assert failed.state is state
                assert failed.error_category == category.value
        finally:
            await client.close()

    asyncio.run(scenario())
