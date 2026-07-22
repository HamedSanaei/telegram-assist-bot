"""T054 concurrent expansion, collision resolution, and publication invariants."""

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
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoAdvertisementSlotRepository,
    MongoPublicationCollisionRepository,
)

NOW = datetime(2026, 7, 22, 8, tzinfo=UTC)
DESTINATION = -100702


class Clock:
    def utc_now(self) -> datetime:
        return NOW


def slot(campaign: str, priority: int) -> AdvertisementSlot:
    return AdvertisementSlot(
        advertisement_slot_identity(campaign, DESTINATION, NOW),
        campaign,
        "destination-fa",
        DESTINATION,
        NOW,
        NOW,
        "UTC",
        f"snapshot-{campaign}",
        1,
        "concurrency-config",
        priority,
        300,
        2,
        NOW - timedelta(hours=1),
        NOW - timedelta(hours=1),
    )


async def _concurrent_reconcile_and_collision_are_idempotent() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t054_concurrency_{uuid4().hex}"
    database = client[database_name]
    slots = MongoAdvertisementSlotRepository(database["advertisement_slots"])
    collision = MongoPublicationCollisionRepository(
        database["advertisement_slots"],
        database["scheduled_publications"],
        database["schedule_queues"],
    )
    try:
        await slots.initialize_indexes()
        alpha, beta = slot("alpha", 100), slot("beta", 10)
        await asyncio.gather(
            slots.reconcile_campaign_slots(
                "alpha", (alpha,), (), now=NOW - timedelta(minutes=1)
            ),
            slots.reconcile_campaign_slots(
                "alpha", (alpha,), (), now=NOW - timedelta(minutes=1)
            ),
            slots.reconcile_campaign_slots(
                "beta", (beta,), (), now=NOW - timedelta(minutes=1)
            ),
        )
        resolver = ResolvePublicationCollision(collision, Clock(), max_cas_attempts=3)
        await asyncio.gather(
            resolver.execute(DESTINATION),
            resolver.execute(DESTINATION),
        )
        persisted = await slots.list_campaign_slots("alpha")
        beta_persisted = await slots.list_campaign_slots("beta")
        assert len(persisted) == 1
        assert len(beta_persisted) == 1
        assert persisted[0].effective_due_at == NOW
        assert beta_persisted[0].effective_due_at == NOW + timedelta(minutes=5)
        winners = await asyncio.gather(
            slots.claim_due_slot(
                owner="worker-a", now=NOW, lease_until=NOW + timedelta(seconds=30)
            ),
            slots.claim_due_slot(
                owner="worker-b", now=NOW, lease_until=NOW + timedelta(seconds=30)
            ),
        )
        assert sum(item is not None for item in winners) == 1
        assert (
            await database["advertisement_slots"].count_documents(
                {"document_type": "advertisement_slot"}
            )
            == 2
        )
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_phase_two_concurrent_reconcile_resolve_and_claim_are_single_winner() -> None:
    asyncio.run(_concurrent_reconcile_and_collision_are_idempotent())
