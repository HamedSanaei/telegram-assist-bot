"""T054 successful Phase Two path on real MongoDB with fake gateways."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.advertisements.expand_advertisement_slots import (
    ExpandAdvertisementSlots,
)
from telegram_assist_bot.application.advertisements.publish_advertisement_slot import (
    AdvertisementPublicationContext,
    PublishAdvertisementSlot,
    PublishAdvertisementSlotStatus,
)
from telegram_assist_bot.application.advertisements.report_advertisement_runs import (
    RenderAdvertisementReport,
    ReportAdvertisementRuns,
)
from telegram_assist_bot.application.advertisements.resolve_publication_collision import (  # noqa: E501
    ResolvePublicationCollision,
)
from telegram_assist_bot.application.config.advertisements import (
    map_advertisement_campaign,
)
from telegram_assist_bot.application.ports import (
    AdvertisementReportKind,
    PublicationPayload,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementMediaReference,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.domain.publication import PublishedMessage
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoAdvertisementRepository,
    MongoAdvertisementSlotRepository,
    MongoPublicationCollisionRepository,
    MongoPublicationRepository,
    initialize_publication_indexes,
)
from telegram_assist_bot.shared.config.models import AdvertisementCampaignConfig

NOW = datetime(2026, 7, 22, 8, tzinfo=UTC)
FIXTURE = Path("tests/fixtures/advertisements/phase_two_campaign.json")


class Clock:
    def __init__(self, now: datetime = NOW) -> None:
        self.now = now

    def utc_now(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class FakeUserPublisher:
    def __init__(self) -> None:
        self.payloads: list[PublicationPayload] = []

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        assert timeout_seconds == 10
        self.payloads.append(payload)
        return PublishedMessage((501, 502), NOW + timedelta(seconds=5))


def load_fixture() -> dict[str, Any]:
    return cast("dict[str, Any]", json.loads(FIXTURE.read_text(encoding="utf-8")))


def make_snapshot(data: dict[str, Any]) -> AdvertisementSourceSnapshot:
    campaign = data["campaign"]
    media = data["media"]
    return AdvertisementSourceSnapshot(
        snapshot_id="phase-two-snapshot-v1",
        campaign_id=campaign["campaign_id"],
        source_identity=AdvertisementSourceIdentity.create(
            campaign["campaign_id"], campaign["source_channel_username"], 123
        ),
        snapshot_version=1,
        snapshot_contract_version="1.0.0",
        content_hash="phase-two-safe-hash",
        text=None,
        caption=data["caption"],
        text_entities=(),
        caption_entities=(
            TelegramEntity(37, 2, "custom_emoji", data["custom_emoji_id"]),
        ),
        media_group_id="phase-two-album",
        media_references=tuple(
            AdvertisementMediaReference(
                MediaType(item["type"]),
                item["index"],
                100 + item["index"],
                "image/jpeg" if item["type"] == "Photo" else "video/mp4",
                f"item-{item['index']}",
                item["path"],
                "phase-two-album",
            )
            for item in media
        ),
        source_published_at=NOW - timedelta(days=1),
        source_edited_at=None,
        fetched_at=NOW - timedelta(hours=1),
        last_successful_fetch_at=NOW - timedelta(hours=1),
    )


async def _complete_phase_two_flow() -> None:
    data = load_fixture()
    campaign = map_advertisement_campaign(
        AdvertisementCampaignConfig.model_validate(data["campaign"])
    )
    destination_id = data["destination_id"]
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        os.environ["TEST_MONGODB_URI"], tz_aware=True
    )
    database_name = f"tab_t054_e2e_{uuid4().hex}"
    database = client[database_name]
    sources = MongoAdvertisementRepository(database["advertisement_sources"])
    slots = MongoAdvertisementSlotRepository(database["advertisement_slots"])
    publications = MongoPublicationRepository(database["publications"])
    collision = MongoPublicationCollisionRepository(
        database["advertisement_slots"],
        database["scheduled_publications"],
        database["schedule_queues"],
    )
    try:
        await sources.initialize_indexes()
        await slots.initialize_indexes()
        await initialize_publication_indexes(
            database["publications"],
            database["scheduled_publications"],
            database["schedule_queues"],
        )
        snapshot = await sources.save_initial_snapshot(make_snapshot(data))
        expanded = await ExpandAdvertisementSlots(slots, Clock()).execute(
            campaign, snapshot, {"destination-fa": destination_id}
        )
        assert len(expanded.slots) == 1
        await database["scheduled_publications"].insert_one(
            {
                "_id": "normal-publication",
                "post_id": "normal-post",
                "destination_id": destination_id,
                "action": "scheduled",
                "due_at": NOW + timedelta(minutes=2),
                "status": "Pending",
                "version": 0,
                "attempt_count": 0,
            }
        )
        await database["schedule_queues"].insert_one(
            {
                "_id": destination_id,
                "slots": [
                    {
                        "job_id": "normal-publication",
                        "due_at": NOW + timedelta(minutes=2),
                    }
                ],
                "last_due_at": NOW + timedelta(minutes=2),
            }
        )
        resolved = await ResolvePublicationCollision(
            collision, Clock(), max_cas_attempts=3
        ).execute(destination_id)
        assert resolved.advertisement_move_count == 1
        assert resolved.normal_move_count == 1
        (moved_slot,) = await slots.list_campaign_slots(campaign.campaign_id)
        assert moved_slot.effective_due_at == NOW
        normal = await database["scheduled_publications"].find_one(
            {"_id": "normal-publication"}
        )
        assert normal is not None
        assert normal["due_at"] == NOW + timedelta(minutes=5)
        publisher = FakeUserPublisher()
        publication_clock = Clock(moved_slot.effective_due_at)
        result = await PublishAdvertisementSlot(
            slots,
            sources,
            publications,
            publisher,
            owner="phase-two-worker",
            clock=publication_clock.utc_now,
            sleeper=publication_clock.sleep,
            timeout_seconds=10,
            lease_seconds=30,
            retry_initial_delay_seconds=1,
            retry_maximum_delay_seconds=5,
            busy_retry_delay_seconds=2,
        ).execute_once(AdvertisementPublicationContext({destination_id}, True, True))
        assert result is PublishAdvertisementSlotStatus.COMPLETED
        assert len(publisher.payloads) == 1
        payload = publisher.payloads[0]
        assert payload.text == data["caption"]
        assert payload.entities[0].custom_emoji_id == data["custom_emoji_id"]
        assert [item.storage_path for item in payload.media] == [
            "sanitized-cache/one.jpg",
            "sanitized-cache/two.mp4",
        ]
        report = await ReportAdvertisementRuns(
            slots,
            timezone="Asia/Tehran",
            upcoming_horizon_days=7,
            failure_horizon_days=7,
            max_items=20,
            clock=lambda: NOW + timedelta(minutes=1),
        ).execute(
            AdvertisementReportKind.TODAY,
            allowed_destination_ids=frozenset({destination_id}),
        )
        rendered = RenderAdvertisementReport().render(report)
        assert "phase-two-ad" in rendered
        assert "شناسه پیام: 501, 502" in rendered
        assert await database["publications"].count_documents({}) == 1
    finally:
        await client.drop_database(database_name)
        await client.close()


def test_phase_two_configuration_to_authorized_report_end_to_end() -> None:
    asyncio.run(_complete_phase_two_flow())
