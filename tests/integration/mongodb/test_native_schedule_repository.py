from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.ports import (
    NativeScheduleReceipt,
    NativeScheduleStatus,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoNativeScheduleRepository,
    initialize_native_schedule_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_native_commands_are_unique_leased_and_leave_legacy_jobs_untouched(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            commands = database["native_schedule_commands"]
            leases = database["native_schedule_destination_leases"]
            legacy = database["scheduled_publications"]
            await initialize_native_schedule_indexes(commands, leases)
            now = datetime(2026, 7, 13, 12, tzinfo=UTC)
            await legacy.insert_one({"_id": "legacy", "action": "scheduled"})
            first = MongoNativeScheduleRepository(commands, leases)
            second = MongoNativeScheduleRepository(commands, leases)
            one, duplicate = await asyncio.gather(
                first.reserve(
                    post_id="post",
                    destination_id=-1001,
                    selection_version=2,
                    now=now,
                ),
                second.reserve(
                    post_id="post",
                    destination_id=-1001,
                    selection_version=2,
                    now=now,
                ),
            )
            assert one.command_id == duplicate.command_id
            claims = await asyncio.gather(
                first.claim_next(
                    owner="one", now=now, lease_until=now + timedelta(minutes=2)
                ),
                second.claim_next(
                    owner="two", now=now, lease_until=now + timedelta(minutes=2)
                ),
            )
            claimed = next(value for value in claims if value is not None)
            assert sum(value is not None for value in claims) == 1
            owner = "one" if claims[0] is not None else "two"
            repository = first if owner == "one" else second
            assert await repository.acquire_destination(
                -1001,
                owner=owner,
                now=now,
                lease_until=now + timedelta(minutes=2),
            )
            assert not await second.acquire_destination(
                -1001,
                owner="competitor",
                now=now,
                lease_until=now + timedelta(minutes=2),
            )
            assert await repository.mark_request_started(
                claimed.command_id,
                owner=owner,
                due_at=now + timedelta(minutes=5),
            )
            completed = await repository.complete_scheduled(
                claimed.command_id,
                owner=owner,
                receipt=NativeScheduleReceipt((80, 81), now + timedelta(minutes=5)),
                now=now,
            )
            assert completed.status is NativeScheduleStatus.SCHEDULED
            assert completed.telegram_message_ids == (80, 81)
            assert await legacy.find_one({"_id": "legacy"}) == {
                "_id": "legacy",
                "action": "scheduled",
            }
        finally:
            await client.close()

    asyncio.run(scenario())


def test_expired_post_request_native_claim_becomes_outcome_unknown(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            commands = database["native_schedule_commands"]
            leases = database["native_schedule_destination_leases"]
            repository = MongoNativeScheduleRepository(commands, leases)
            now = datetime(2026, 7, 13, 12, tzinfo=UTC)
            command = await repository.reserve(
                post_id="ambiguous",
                destination_id=-1002,
                selection_version=1,
                now=now,
            )
            claimed = await repository.claim_next(
                owner="crashed",
                now=now,
                lease_until=now + timedelta(seconds=1),
            )
            assert claimed is not None
            assert await repository.mark_request_started(
                command.command_id,
                owner="crashed",
                due_at=now + timedelta(minutes=5),
            )
            assert (
                await repository.claim_next(
                    owner="restart",
                    now=now + timedelta(seconds=2),
                    lease_until=now + timedelta(minutes=2),
                )
                is None
            )
            document = await commands.find_one({"_id": command.command_id})
            assert document is not None
            assert document["status"] == NativeScheduleStatus.OUTCOME_UNKNOWN.value
        finally:
            await client.close()

    asyncio.run(scenario())


def test_cancellation_racing_with_schedule_request_is_deleted_after_receipt(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            repository = MongoNativeScheduleRepository(
                database["native_schedule_commands"],
                database["native_schedule_destination_leases"],
            )
            now = datetime(2026, 7, 13, 12, tzinfo=UTC)
            command = await repository.reserve(
                post_id="race",
                destination_id=-1003,
                selection_version=4,
                now=now,
            )
            assert await repository.claim_next(
                owner="runtime",
                now=now,
                lease_until=now + timedelta(minutes=2),
            )
            assert await repository.mark_request_started(
                command.command_id,
                owner="runtime",
                due_at=now + timedelta(minutes=5),
            )
            cancellation = await repository.request_cancel_latest(
                post_id="race",
                destination_id=-1003,
                now=now,
                follow_up_immediate=True,
            )
            assert cancellation is not None
            completed = await repository.complete_scheduled(
                command.command_id,
                owner="runtime",
                receipt=NativeScheduleReceipt((91,), now + timedelta(minutes=5)),
                now=now,
            )
            assert completed.status is NativeScheduleStatus.CANCEL_REQUESTED
            assert completed.operation == "cancel"
            assert completed.telegram_message_ids == (91,)
            claimed = await repository.claim_next(
                owner="runtime",
                now=now,
                lease_until=now + timedelta(minutes=2),
            )
            assert claimed is not None
            assert claimed.operation == "cancel"
            assert claimed.follow_up_immediate
        finally:
            await client.close()

    asyncio.run(scenario())
