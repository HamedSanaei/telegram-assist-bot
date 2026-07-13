"""MongoDB integration proofs for delivery claims and due-now commands."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pymongo import AsyncMongoClient

from telegram_assist_bot.domain import publication_identity
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoApprovalPostLoader,
    MongoOperationalApprovalRepository,
    MongoRuntimeHeartbeatRepository,
    MongoScheduleRepository,
    initialize_operational_approval_indexes,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_delivery_repository_rejects_unbounded_retry_configuration() -> None:
    with pytest.raises(ValueError, match="max_attempts"):
        MongoOperationalApprovalRepository(object(), object(), max_attempts=0)  # type: ignore[arg-type]


def test_runtime_heartbeat_freshness_and_staleness(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            collection = client[mongodb_test_settings.database_name][
                "runtime_heartbeats"
            ]
            repository = MongoRuntimeHeartbeatRepository(collection)
            now = datetime(2026, 7, 13, 12, tzinfo=UTC)
            await repository.beat(
                "runtime-safe-id", started_at=now, now=now, status="running"
            )
            assert await repository.is_active(now=now, stale_after_seconds=15)
            assert not await repository.is_active(
                now=now + timedelta(seconds=16), stale_after_seconds=15
            )
            stored = await collection.find_one({"_id": "runtime-safe-id"})
            assert stored is not None
            assert set(stored) == {
                "_id",
                "instance_id",
                "started_at",
                "last_seen_at",
                "status",
            }
            await repository.beat(
                "runtime-safe-id",
                started_at=now,
                now=now + timedelta(seconds=17),
                status="stopped",
            )
            assert not await repository.is_active(
                now=now + timedelta(seconds=17), stale_after_seconds=15
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_ready_delivery_is_claimed_once_and_expired_lease_recovers(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            preparations = database["content_preparations"]
            deliveries = database["approval_deliveries"]
            await initialize_operational_approval_indexes(deliveries)
            now = datetime(2026, 7, 13, tzinfo=UTC)
            await preparations.insert_one({"_id": "ready-1", "ready_at": now})
            first = MongoOperationalApprovalRepository(preparations, deliveries)
            second = MongoOperationalApprovalRepository(preparations, deliveries)
            claims = await asyncio.gather(
                first.claim_ready(
                    owner="one", now=now, lease_until=now + timedelta(seconds=30)
                ),
                second.claim_ready(
                    owner="two", now=now, lease_until=now + timedelta(seconds=30)
                ),
            )
            assert sum(item is not None for item in claims) == 1
            assert await deliveries.count_documents({"_id": "ready-1"}) == 1
            recovered = await second.claim_ready(
                owner="two",
                now=now + timedelta(seconds=31),
                lease_until=now + timedelta(seconds=61),
            )
            assert recovered is not None
            assert recovered.owner == "two"
            assert await second.is_actionable("ready-1")
            assert await second.release_delivery(
                "ready-1",
                owner="two",
                category="transient",
                next_attempt_at=now + timedelta(seconds=62),
            )
            final = await first.claim_ready(
                owner="one",
                now=now + timedelta(seconds=63),
                lease_until=now + timedelta(seconds=93),
            )
            assert final is not None
            assert await first.complete_delivery("ready-1", owner="one")
            await first.record_destination_status(
                "ready-1",
                -1001,
                status="published",
                version=1,
                at=now + timedelta(seconds=64),
            )
            assert await first.destination_statuses("ready-1") == {-1001: "published"}
            sync = await first.claim_sync(
                owner="sync",
                now=now + timedelta(seconds=64),
                lease_until=now + timedelta(seconds=94),
            )
            assert sync is not None
            assert await first.complete_sync(
                "ready-1", owner="sync", version=sync.version
            )

            await preparations.insert_one({"_id": "bounded", "ready_at": now})
            bounded = MongoOperationalApprovalRepository(
                preparations, deliveries, max_attempts=1
            )
            bounded_claim = await bounded.claim_ready(
                owner="bounded",
                now=now,
                lease_until=now + timedelta(seconds=30),
            )
            assert bounded_claim is not None
            assert await bounded.release_delivery(
                "bounded",
                owner="bounded",
                category="transient",
                next_attempt_at=now,
            )
            assert (
                await bounded.claim_ready(
                    owner="bounded",
                    now=now + timedelta(seconds=31),
                    lease_until=now + timedelta(seconds=61),
                )
                is None
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_approval_loader_preserves_prepared_persian_entities_and_media(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            now = datetime(2026, 7, 13, tzinfo=UTC)
            await database["posts"].insert_one(
                {
                    "_id": "prepared-1",
                    "source_channel_id": -2001,
                    "source_message_id": 10,
                    "source_channel_display_name": "منبع فارسی",
                    "source_channel_username": "source",
                    "source_published_at": now - timedelta(hours=1),
                    "original_content": {
                        "text": "متن اصلی",
                        "caption": None,
                        "text_entities": [],
                        "caption_entities": [],
                    },
                }
            )
            await database["content_preparations"].insert_one(
                {
                    "_id": "prepared-1",
                    "ready_at": now,
                    "category_result": {"category_id": "خبر"},
                    "duplicate_result": {"is_duplicate": False},
                    "artifacts": {
                        "dest": {
                            "text": "سلام\nایران 🇮🇷",  # noqa: RUF001
                            "entities": [
                                {
                                    "offset": 0,
                                    "length": 4,
                                    "entity_type": "bold",
                                    "custom_emoji_id": None,
                                }
                            ],
                        }
                    },
                }
            )
            await database["media_items"].insert_one(
                {
                    "_id": "media-1",
                    "source_channel_id": -2001,
                    "source_message_id": 10,
                    "item_index": 0,
                    "storage_path": "post/photo.jpg",
                    "media_type": "photo",
                    "cleaned_at": None,
                }
            )
            loader = MongoApprovalPostLoader(
                database["posts"],
                database["content_preparations"],
                database["media_items"],
                database["media_groups"],
                destination_names=("dest",),
            )
            loaded = await loader.load("prepared-1")
            assert loaded.content.text == "سلام\nایران 🇮🇷"  # noqa: RUF001
            assert loaded.content.text_entities[0].entity_type == "bold"
            assert loaded.content.media_paths == ("post/photo.jpg",)
            assert loaded.category == "خبر"
            assert loaded.duplicate == "خیر"
            assert loaded.source_message_id == 10
            assert loaded.source_published_at == now - timedelta(hours=1)
            assert loaded.content_type == "photo"
            assert loaded.media_count == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_immediate_command_is_unique_due_now_and_does_not_advance_schedule_queue(
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
            repository = MongoScheduleRepository(schedules, queues)
            now = datetime(2026, 7, 13, tzinfo=UTC)
            identity = publication_identity("post-1", -1001, "immediate")
            results = await asyncio.gather(
                *(
                    repository.reserve_immediate(
                        job_id=identity,
                        post_id="post-1",
                        destination_id=-1001,
                        now=now,
                    )
                    for _ in range(8)
                )
            )
            assert sum(item.created for item in results) == 1
            assert results[0].job.due_at == now
            assert results[0].job.action == "immediate"
            assert await queues.count_documents({}) == 0
            claimed = await repository.claim_due(
                owner="runtime-after-restart",
                now=now,
                lease_until=now + timedelta(seconds=30),
                action="immediate",
            )
            assert claimed is not None
            assert claimed.job_id == identity
            assert (
                await repository.claim_due(
                    owner="competing-runtime",
                    now=now,
                    lease_until=now + timedelta(seconds=30),
                    action="immediate",
                )
                is None
            )
            assert await repository.complete(
                identity, owner="runtime-after-restart", at=now
            )
            assert (
                await repository.claim_due(
                    owner="runtime-after-restart",
                    now=now + timedelta(seconds=31),
                    lease_until=now + timedelta(seconds=61),
                    action="immediate",
                )
                is None
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_future_scheduled_job_is_not_claimed_early_and_due_job_is_claimed_once(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            schedules = database["scheduled_publications"]
            queues = database["schedule_queues"]
            await initialize_publication_indexes(
                database["publications"], schedules, queues
            )
            repository = MongoScheduleRepository(schedules, queues)
            now = datetime(2026, 7, 13, 12, tzinfo=UTC)
            reservation = await repository.reserve(
                job_id="future-scheduled",
                post_id="post-future",
                destination_id=-1001,
                now=now,
                interval=timedelta(minutes=5),
            )
            assert reservation.job.due_at == now + timedelta(minutes=5)
            assert (
                await repository.claim_due(
                    owner="runtime",
                    now=now + timedelta(minutes=4, seconds=59),
                    lease_until=now + timedelta(minutes=6),
                    action="scheduled",
                )
                is None
            )
            claims = await asyncio.gather(
                repository.claim_due(
                    owner="runtime-one",
                    now=reservation.job.due_at,
                    lease_until=reservation.job.due_at + timedelta(seconds=30),
                    action="scheduled",
                ),
                repository.claim_due(
                    owner="runtime-two",
                    now=reservation.job.due_at,
                    lease_until=reservation.job.due_at + timedelta(seconds=30),
                    action="scheduled",
                ),
            )
            assert sum(item is not None for item in claims) == 1
        finally:
            await client.close()

    asyncio.run(scenario())
