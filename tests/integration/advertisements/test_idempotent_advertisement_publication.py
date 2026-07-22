"""Real MongoDB workflow tests for T051 advertisement publication."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.advertisements.publish_advertisement_slot import (
    AdvertisementPublicationContext,
    PublishAdvertisementSlot,
    PublishAdvertisementSlotStatus,
)
from telegram_assist_bot.application.ports import PublicationPayload, PublisherError
from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementSlot,
    AdvertisementSlotStatus,
    advertisement_slot_identity,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementMediaReference,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.domain.publication import (
    PublicationFailureCategory,
    PublishedMessage,
)
from telegram_assist_bot.domain.publication_collision import CollisionResolutionState
from telegram_assist_bot.infrastructure.persistence.mongodb.advertisement_repository import (  # noqa: E501
    MongoAdvertisementRepository,
    MongoAdvertisementSlotRepository,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_repository import (  # noqa: E501
    MongoPublicationRepository,
    initialize_publication_indexes,
)

START = datetime(2026, 7, 22, 10, tzinfo=UTC)
DESTINATION_ID = -100900


class MutableClock:
    def __init__(self, now: datetime = START) -> None:
        self.now = now

    def utc_now(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakePublisher:
    def __init__(self, errors: list[PublisherError] | None = None) -> None:
        self.errors = errors or []
        self.payloads: list[PublicationPayload] = []

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        assert timeout_seconds == 10
        self.payloads.append(payload)
        await asyncio.sleep(0)
        if self.errors:
            raise self.errors.pop(0)
        return PublishedMessage((901, 902), START + timedelta(seconds=75))


def snapshot() -> AdvertisementSourceSnapshot:
    return AdvertisementSourceSnapshot(
        snapshot_id="snapshot-t051-v1",
        campaign_id="campaign-t051",
        source_identity=AdvertisementSourceIdentity.create(
            "campaign-t051", "sample_ads", 51
        ),
        snapshot_version=1,
        snapshot_contract_version="1.0.0",
        content_hash="hash-t051",
        text=None,
        caption="آگهی پایدار با نیم\u200cفاصله و ایموجی ✨",  # noqa: RUF001
        text_entities=(),
        caption_entities=(TelegramEntity(31, 2, "custom_emoji", "456789"),),
        media_group_id="album-t051",
        media_references=(
            AdvertisementMediaReference(
                MediaType.PHOTO, 0, 10, "image/jpeg", "one.jpg", "cache/one"
            ),
            AdvertisementMediaReference(
                MediaType.VIDEO, 1, 20, "video/mp4", "two.mp4", "cache/two"
            ),
        ),
        source_published_at=START - timedelta(days=2),
        source_edited_at=None,
        fetched_at=START - timedelta(hours=1),
        last_successful_fetch_at=START - timedelta(hours=1),
    )


def slot() -> AdvertisementSlot:
    due_at = START - timedelta(seconds=30)
    return AdvertisementSlot(
        slot_id=advertisement_slot_identity("campaign-t051", DESTINATION_ID, due_at),
        campaign_id="campaign-t051",
        destination_name="news",
        destination_id=DESTINATION_ID,
        due_at=due_at,
        local_scheduled_at=due_at,
        timezone_name="UTC",
        source_snapshot_id="snapshot-t051-v1",
        source_snapshot_version=1,
        config_fingerprint="config-t051",
        priority=1,
        minimum_gap_seconds=300,
        max_retries=2,
        created_at=START - timedelta(hours=1),
        updated_at=START - timedelta(hours=1),
        collision_state=CollisionResolutionState.RESOLVED,
    )


def worker(
    slots: MongoAdvertisementSlotRepository,
    snapshots: MongoAdvertisementRepository,
    publications: MongoPublicationRepository,
    publisher: FakePublisher,
    clock: MutableClock,
    owner: str,
) -> PublishAdvertisementSlot:
    return PublishAdvertisementSlot(
        slots,
        snapshots,
        publications,
        publisher,
        owner=owner,
        clock=clock.utc_now,
        sleeper=clock.sleep,
        timeout_seconds=10,
        lease_seconds=30,
        retry_initial_delay_seconds=1,
        retry_maximum_delay_seconds=5,
        busy_retry_delay_seconds=2,
    )


async def prepare(
    database: object,
) -> tuple[
    MongoAdvertisementSlotRepository,
    MongoAdvertisementRepository,
    MongoPublicationRepository,
]:
    slots = MongoAdvertisementSlotRepository(database["advertisement_slots"])  # type: ignore[index]
    snapshots = MongoAdvertisementRepository(database["advertisement_sources"])  # type: ignore[index]
    publications = MongoPublicationRepository(database["publications"])  # type: ignore[index]
    await slots.initialize_indexes()
    await snapshots.initialize_indexes()
    await initialize_publication_indexes(
        database["publications"],  # type: ignore[index]
        database["schedules"],  # type: ignore[index]
        database["queues"],  # type: ignore[index]
    )
    await snapshots.save_initial_snapshot(snapshot())
    await slots.reconcile_campaign_slots(
        "campaign-t051", (slot(),), (), now=START - timedelta(minutes=1)
    )
    return slots, snapshots, publications


async def _competing_workers_restart_and_audit() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t051_{uuid4().hex}"
    database = client[database_name]
    try:
        slots, snapshots, publications = await prepare(database)
        publisher = FakePublisher()
        first_clock, second_clock = MutableClock(), MutableClock()
        results = await asyncio.gather(
            worker(
                slots, snapshots, publications, publisher, first_clock, "worker-one"
            ).execute_once(
                AdvertisementPublicationContext((DESTINATION_ID,), True, True)
            ),
            worker(
                slots, snapshots, publications, publisher, second_clock, "worker-two"
            ).execute_once(
                AdvertisementPublicationContext((DESTINATION_ID,), True, True)
            ),
        )

        assert PublishAdvertisementSlotStatus.COMPLETED in results
        assert len(publisher.payloads) == 1
        stored = (await slots.list_campaign_slots("campaign-t051"))[0]
        assert stored.status is AdvertisementSlotStatus.COMPLETED
        assert stored.message_ids == (901, 902)
        assert stored.publication_attempt_count == 1
        assert stored.execution_delay_seconds == 105
        assert stored.published_at == START + timedelta(seconds=75)

        restarted = await worker(
            slots,
            snapshots,
            publications,
            publisher,
            MutableClock(START + timedelta(minutes=5)),
            "worker-restart",
        ).execute_once(AdvertisementPublicationContext((DESTINATION_ID,), True, True))
        assert restarted is PublishAdvertisementSlotStatus.IDLE
        assert len(publisher.payloads) == 1
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _expired_lease_is_recovered_after_restart() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t051_{uuid4().hex}"
    database = client[database_name]
    try:
        slots, snapshots, publications = await prepare(database)
        claimed = await slots.claim_due_slot(
            owner="crashed-worker",
            now=START,
            lease_until=START + timedelta(seconds=30),
        )
        assert claimed is not None
        publisher = FakePublisher()
        result = await worker(
            slots,
            snapshots,
            publications,
            publisher,
            MutableClock(START + timedelta(seconds=31)),
            "replacement-worker",
        ).execute_once(AdvertisementPublicationContext((DESTINATION_ID,), True, True))

        assert result is PublishAdvertisementSlotStatus.COMPLETED
        stored = (await slots.list_campaign_slots("campaign-t051"))[0]
        assert stored.claim_count == 2
        assert len(publisher.payloads) == 1
    finally:
        await client.drop_database(database_name)
        await client.close()


async def _ambiguous_timeout_is_persisted_without_second_send() -> None:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t051_{uuid4().hex}"
    database = client[database_name]
    try:
        slots, snapshots, publications = await prepare(database)
        publisher = FakePublisher(
            [
                PublisherError(
                    PublicationFailureCategory.TIMEOUT,
                    request_may_have_reached_telegram=True,
                    reason_code="outcome_unknown",
                )
            ]
        )
        use_case = worker(
            slots, snapshots, publications, publisher, MutableClock(), "worker-one"
        )
        first = await use_case.execute_once(
            AdvertisementPublicationContext((DESTINATION_ID,), True, True)
        )
        second = await use_case.execute_once(
            AdvertisementPublicationContext((DESTINATION_ID,), True, True)
        )

        assert first is PublishAdvertisementSlotStatus.OUTCOME_UNKNOWN
        assert second is PublishAdvertisementSlotStatus.IDLE
        assert len(publisher.payloads) == 1
        stored = (await slots.list_campaign_slots("campaign-t051"))[0]
        assert stored.status is AdvertisementSlotStatus.OUTCOME_UNKNOWN
        assert stored.last_error_category == PublicationFailureCategory.TIMEOUT.value
        assert stored.last_failure_reason_code == "outcome_unknown"
        serialized = str(await database["advertisement_slots"].find_one({}))
        assert "Authorization" not in serialized
        assert "BOT_SECRET" not in serialized
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_competing_workers_restart_and_audit() -> None:
    asyncio.run(_competing_workers_restart_and_audit())


def test_expired_lease_is_recovered_after_restart() -> None:
    asyncio.run(_expired_lease_is_recovered_after_restart())


def test_ambiguous_timeout_is_persisted_without_second_send() -> None:
    asyncio.run(_ambiguous_timeout_is_persisted_without_second_send())
