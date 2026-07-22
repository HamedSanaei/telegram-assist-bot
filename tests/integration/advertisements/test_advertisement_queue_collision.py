"""Real MongoDB tests for T052 collision CAS, restart, and queue preservation."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.advertisements.resolve_publication_collision import (  # noqa: E501
    ResolvePublicationCollision,
)
from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementSlot,
    advertisement_slot_identity,
)
from telegram_assist_bot.domain.publication_collision import CollisionResolutionOutcome
from telegram_assist_bot.domain.scheduling import ScheduleStatus
from telegram_assist_bot.infrastructure.persistence.mongodb.advertisement_repository import (  # noqa: E501
    MongoAdvertisementSlotRepository,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_collision_repository import (  # noqa: E501
    MongoPublicationCollisionRepository,
)

NOW = datetime(2026, 7, 22, 10, tzinfo=UTC)
DESTINATION_ID = -100700


class FixedClock:
    def utc_now(self) -> datetime:
        return NOW


def slot(campaign_id: str, due_at: datetime, *, priority: int) -> AdvertisementSlot:
    return AdvertisementSlot(
        slot_id=advertisement_slot_identity(campaign_id, DESTINATION_ID, due_at),
        campaign_id=campaign_id,
        destination_name="news",
        destination_id=DESTINATION_ID,
        due_at=due_at,
        local_scheduled_at=due_at,
        timezone_name="UTC",
        source_snapshot_id=f"snapshot-{campaign_id}",
        source_snapshot_version=1,
        config_fingerprint="config-t052",
        priority=priority,
        minimum_gap_seconds=300,
        max_retries=2,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )


async def _concurrent_resolution_restart_and_claim_boundary() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t052_{uuid4().hex}"
    database = client[database_name]
    slots = database["advertisement_slots"]
    schedules = database["scheduled_publications"]
    queues = database["schedule_queues"]
    slot_repository = MongoAdvertisementSlotRepository(slots)
    collision_repository = MongoPublicationCollisionRepository(slots, schedules, queues)
    try:
        await slot_repository.initialize_indexes()
        high = slot("campaign-alpha", NOW, priority=5)
        low = slot("campaign-beta", NOW, priority=1)
        await slot_repository.reconcile_campaign_slots(
            high.campaign_id, (high,), (), now=NOW - timedelta(minutes=10)
        )
        await slot_repository.reconcile_campaign_slots(
            low.campaign_id, (low,), (), now=NOW - timedelta(minutes=10)
        )
        normal_docs = [
            {
                "_id": "normal-one",
                "post_id": "post-one",
                "destination_id": DESTINATION_ID,
                "action": "scheduled",
                "due_at": NOW - timedelta(minutes=2),
                "status": ScheduleStatus.PENDING.value,
                "version": 0,
                "attempt_count": 0,
            },
            {
                "_id": "normal-two",
                "post_id": "post-two",
                "destination_id": DESTINATION_ID,
                "action": "scheduled",
                "due_at": NOW + timedelta(minutes=2),
                "status": ScheduleStatus.PENDING.value,
                "version": 0,
                "attempt_count": 0,
            },
        ]
        await schedules.insert_many(normal_docs)
        await queues.insert_one(
            {
                "_id": DESTINATION_ID,
                "slots": [
                    {"job_id": item["_id"], "due_at": item["due_at"]}
                    for item in normal_docs
                ],
                "last_due_at": normal_docs[-1]["due_at"],
            }
        )

        resolver = ResolvePublicationCollision(
            collision_repository, FixedClock(), max_cas_attempts=3
        )
        first, second = await asyncio.gather(
            resolver.execute(DESTINATION_ID), resolver.execute(DESTINATION_ID)
        )
        assert {
            first.outcome,
            second.outcome,
        } <= {
            CollisionResolutionOutcome.RESOLVED,
            CollisionResolutionOutcome.ALREADY_RESOLVED,
        }

        stored_slots = {
            item.campaign_id: item
            for item in await slot_repository.list_campaign_slots("campaign-alpha")
            + await slot_repository.list_campaign_slots("campaign-beta")
        }
        assert stored_slots["campaign-alpha"].effective_due_at == NOW
        assert stored_slots["campaign-beta"].effective_due_at == NOW + timedelta(
            minutes=5
        )
        assert len(stored_slots["campaign-alpha"].collision_history) == 1
        assert len(stored_slots["campaign-beta"].collision_history) == 1

        normal_one = await schedules.find_one({"_id": "normal-one"})
        normal_two = await schedules.find_one({"_id": "normal-two"})
        assert normal_one is not None
        assert normal_two is not None
        assert normal_one["due_at"] == NOW + timedelta(minutes=10)
        assert normal_two["due_at"] == NOW + timedelta(minutes=14)
        normal_history = normal_one["due_time_history"]
        assert isinstance(normal_history, list)
        assert len(normal_history) == 1
        assert isinstance(normal_history[0], dict)
        assert normal_history[0]["reason"] == ("advertisement_priority_minimum_gap")
        queue = await queues.find_one({"_id": DESTINATION_ID})
        assert queue is not None
        queue_slots = queue["slots"]
        assert isinstance(queue_slots, list)
        assert [item["due_at"] for item in queue_slots] == [
            NOW + timedelta(minutes=10),
            NOW + timedelta(minutes=14),
        ]

        restarted = await ResolvePublicationCollision(
            MongoPublicationCollisionRepository(slots, schedules, queues),
            FixedClock(),
            max_cas_attempts=3,
        ).execute(DESTINATION_ID)
        assert restarted.outcome is CollisionResolutionOutcome.ALREADY_RESOLVED

        before_low_due = NOW + timedelta(minutes=5) - timedelta(microseconds=1)
        assert (
            await slot_repository.claim_due_slot(
                owner="worker",
                now=before_low_due,
                lease_until=before_low_due + timedelta(seconds=30),
            )
        ).campaign_id == "campaign-alpha"  # type: ignore[union-attr]
        assert (
            await slot_repository.claim_due_slot(
                owner="worker-two",
                now=before_low_due,
                lease_until=before_low_due + timedelta(seconds=30),
            )
            is None
        )
        claimed_low = await slot_repository.claim_due_slot(
            owner="worker-two",
            now=NOW + timedelta(minutes=5),
            lease_until=NOW + timedelta(minutes=6),
        )
        assert claimed_low is not None
        assert claimed_low.campaign_id == "campaign-beta"
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _claimed_normal_is_never_moved() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t052_{uuid4().hex}"
    database = client[database_name]
    slot_repository = MongoAdvertisementSlotRepository(database["advertisement_slots"])
    try:
        await slot_repository.initialize_indexes()
        ad = slot("campaign", NOW, priority=1)
        await slot_repository.reconcile_campaign_slots(
            ad.campaign_id, (ad,), (), now=NOW - timedelta(minutes=10)
        )
        await database["scheduled_publications"].insert_one(
            {
                "_id": "executing",
                "post_id": "post",
                "destination_id": DESTINATION_ID,
                "action": "scheduled",
                "due_at": NOW,
                "status": ScheduleStatus.CLAIMED.value,
                "version": 1,
                "attempt_count": 1,
                "claim_owner": "normal-worker",
                "lease_until": NOW + timedelta(minutes=1),
            }
        )
        result = await ResolvePublicationCollision(
            MongoPublicationCollisionRepository(
                database["advertisement_slots"],
                database["scheduled_publications"],
                database["schedule_queues"],
            ),
            FixedClock(),
            max_cas_attempts=3,
        ).execute(DESTINATION_ID)
        executing = await database["scheduled_publications"].find_one(
            {"_id": "executing"}
        )
        stored = (await slot_repository.list_campaign_slots("campaign"))[0]
        assert result.immutable_conflict_count == 1
        assert executing is not None
        assert executing["due_at"] == NOW
        assert stored.immutable_collision_ids == (f"{stored.slot_id}:executing",)
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_concurrent_resolution_restart_and_claim_boundary() -> None:
    asyncio.run(_concurrent_resolution_restart_and_claim_boundary())


def test_claimed_normal_is_never_moved() -> None:
    asyncio.run(_claimed_normal_is_never_moved())
