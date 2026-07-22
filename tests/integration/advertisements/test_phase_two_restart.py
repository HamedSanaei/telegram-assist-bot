"""T054 restart recovery for immutable snapshots and durable advertisement slots."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import AsyncMongoClient

from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementSlot,
    advertisement_slot_identity,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.publication_collision import CollisionResolutionState
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoAdvertisementRepository,
    MongoAdvertisementSlotRepository,
)

NOW = datetime(2026, 7, 22, 8, tzinfo=UTC)
DESTINATION = -100701


def snapshot() -> AdvertisementSourceSnapshot:
    return AdvertisementSourceSnapshot(
        "restart-snapshot-v1",
        "restart-campaign",
        AdvertisementSourceIdentity.create("restart-campaign", "sample_ads", 44),
        1,
        "1.0.0",
        "restart-hash",
        "متن پایدار‌ پس از Restart ✨",
        None,
        (),
        (),
        None,
        (),
        NOW - timedelta(days=1),
        None,
        NOW - timedelta(hours=1),
        NOW - timedelta(hours=1),
    )


def slot() -> AdvertisementSlot:
    return AdvertisementSlot(
        advertisement_slot_identity("restart-campaign", DESTINATION, NOW),
        "restart-campaign",
        "destination-fa",
        DESTINATION,
        NOW,
        NOW,
        "UTC",
        "restart-snapshot-v1",
        1,
        "restart-config",
        10,
        300,
        2,
        NOW - timedelta(hours=1),
        NOW - timedelta(hours=1),
        collision_state=CollisionResolutionState.RESOLVED,
    )


async def _restart_recovers_snapshot_slot_and_expired_lease_once() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t054_restart_{uuid4().hex}"
    database = client[database_name]
    try:
        first_sources = MongoAdvertisementRepository(database["advertisement_sources"])
        first_slots = MongoAdvertisementSlotRepository(database["advertisement_slots"])
        await first_sources.initialize_indexes()
        await first_slots.initialize_indexes()
        stored_snapshot = await first_sources.save_initial_snapshot(snapshot())
        await first_slots.reconcile_campaign_slots(
            "restart-campaign", (slot(),), (), now=NOW - timedelta(minutes=1)
        )
        claimed = await first_slots.claim_due_slot(
            owner="crashed-worker", now=NOW, lease_until=NOW + timedelta(seconds=10)
        )
        assert claimed is not None

        restarted_sources = MongoAdvertisementRepository(
            database["advertisement_sources"]
        )
        restarted_slots = MongoAdvertisementSlotRepository(
            database["advertisement_slots"]
        )
        recovered_snapshot = await restarted_sources.get_snapshot_by_id(
            stored_snapshot.snapshot_id
        )
        recovered = await restarted_slots.claim_due_slot(
            owner="restart-worker",
            now=NOW + timedelta(seconds=10),
            lease_until=NOW + timedelta(seconds=40),
        )
        loser = await restarted_slots.claim_due_slot(
            owner="duplicate-worker",
            now=NOW + timedelta(seconds=10),
            lease_until=NOW + timedelta(seconds=40),
        )
        assert recovered_snapshot == stored_snapshot
        assert recovered is not None
        assert recovered.slot_id == claimed.slot_id
        assert loser is None
        assert (
            await database["advertisement_sources"].count_documents(
                {"is_current": True}
            )
            == 1
        )
        assert (
            await database["advertisement_slots"].count_documents(
                {"document_type": "advertisement_slot"}
            )
            == 1
        )
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_phase_two_restart_preserves_jobs_and_recovers_expired_lease() -> None:
    asyncio.run(_restart_recovers_snapshot_slot_and_expired_lease_once())
