"""Real MongoDB tests for durable advertisement slot expansion."""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from datetime import UTC, date, datetime, time
from uuid import uuid4
from zoneinfo import ZoneInfo

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.advertisements.expand_advertisement_slots import (
    ExpandAdvertisementSlots,
)
from telegram_assist_bot.domain.advertisement_slot import AdvertisementSlotStatus
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.advertisements import (
    AdvertisementCampaign,
    AdvertisementErrorPolicy,
    AdvertisementPublicationMode,
    SourceAdvertisementPost,
    SourceCachePolicy,
    SourceUnavailablePolicy,
    Weekday,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.advertisement_repository import (  # noqa: E501
    MongoAdvertisementSlotRepository,
)


class FixedClock:
    def utc_now(self) -> datetime:
        return datetime(2026, 8, 1, tzinfo=UTC)


def campaign(*, times: tuple[time, ...] = (time(9),)) -> AdvertisementCampaign:
    return AdvertisementCampaign(
        campaign_id="campaign-mongo",
        name="کمپین پایدار",
        enabled=True,
        source_post=SourceAdvertisementPost(
            "https://t.me/sample_ads/42", "sample_ads", 42
        ),
        destination_names=("one", "two"),
        weekdays=(Weekday.SATURDAY, Weekday.SUNDAY),
        times=times,
        start_date=date(2026, 8, 1),
        end_date=date(2026, 8, 2),
        timezone=ZoneInfo("Asia/Tehran"),
        publication_mode=AdvertisementPublicationMode.COPY,
        priority=1,
        minimum_gap_seconds=300,
        error_policy=AdvertisementErrorPolicy.RETRY_THEN_FAIL,
        max_retries=2,
        source_cache_policy=SourceCachePolicy.CACHED,
        source_unavailable_policy=SourceUnavailablePolicy.FAIL_CLOSED,
        snapshot_retention_days=30,
    )


def snapshot() -> AdvertisementSourceSnapshot:
    instant = datetime(2026, 8, 1, tzinfo=UTC)
    return AdvertisementSourceSnapshot(
        snapshot_id="snapshot-mongo-v1",
        campaign_id="campaign-mongo",
        source_identity=AdvertisementSourceIdentity.create(
            "campaign-mongo", "sample_ads", 42
        ),
        snapshot_version=1,
        snapshot_contract_version="1.0.0",
        content_hash="hash-mongo",
        text="متن تبلیغ ✨",
        caption=None,
        text_entities=(),
        caption_entities=(),
        media_group_id=None,
        media_references=(),
        source_published_at=instant,
        source_edited_at=None,
        fetched_at=instant,
        last_successful_fetch_at=instant,
    )


async def _concurrent_expansion_restart_and_indexes_are_idempotent() -> None:
    uri = os.environ["TEST_MONGODB_URI"]
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(uri)
    database_name = f"tab_t050_{uuid4().hex}"
    collection = client[database_name]["advertisement_slots"]
    repository = MongoAdvertisementSlotRepository(collection)
    try:
        await repository.initialize_indexes()
        use_case = ExpandAdvertisementSlots(repository, FixedClock())
        destinations = {"one": -1001, "two": -1002}

        first, second = await asyncio.gather(
            use_case.execute(campaign(), snapshot(), destinations),
            use_case.execute(campaign(), snapshot(), destinations),
        )
        restarted = await ExpandAdvertisementSlots(repository, FixedClock()).execute(
            campaign(), snapshot(), destinations
        )

        assert len(first.slots) == len(second.slots) == len(restarted.slots) == 4
        assert (
            await collection.count_documents({"document_type": "advertisement_slot"})
            == 4
        )
        assert {item.version for item in restarted.slots} == {0}
        index_info = await collection.index_information()
        assert index_info["uq_advertisement_slot_identity"]["unique"] is True
        assert "ix_advertisement_slot_due" in index_info
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _configuration_reconciliation_preserves_completed_slot() -> None:
    uri = os.environ["TEST_MONGODB_URI"]
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(uri)
    database_name = f"tab_t050_{uuid4().hex}"
    collection = client[database_name]["advertisement_slots"]
    repository = MongoAdvertisementSlotRepository(collection)
    try:
        await repository.initialize_indexes()
        use_case = ExpandAdvertisementSlots(repository, FixedClock())
        destinations = {"one": -1001, "two": -1002}
        initial = await use_case.execute(campaign(), snapshot(), destinations)
        completed = initial.slots[-1]
        await collection.update_one(
            {"_id": completed.slot_id},
            {"$set": {"status": AdvertisementSlotStatus.COMPLETED.value}},
        )

        await use_case.execute(
            replace(campaign(), times=(time(10),)), snapshot(), destinations
        )
        stored = await repository.list_campaign_slots("campaign-mongo")

        completed_after = next(
            item for item in stored if item.slot_id == completed.slot_id
        )
        assert completed_after.status is AdvertisementSlotStatus.COMPLETED
        assert completed_after.due_at == completed.due_at
        assert (
            len(
                [
                    item
                    for item in stored
                    if item.status
                    is AdvertisementSlotStatus.CANCELLED_BY_RECONCILIATION
                ]
            )
            == 3
        )
        assert (
            len(
                [
                    item
                    for item in stored
                    if item.status is AdvertisementSlotStatus.SCHEDULED
                ]
            )
            == 4
        )
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_concurrent_expansion_restart_and_indexes_are_idempotent() -> None:
    asyncio.run(_concurrent_expansion_restart_and_indexes_are_idempotent())


def test_configuration_reconciliation_preserves_completed_slot() -> None:
    asyncio.run(_configuration_reconciliation_preserves_completed_slot())
