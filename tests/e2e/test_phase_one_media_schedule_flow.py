"""E2E Scenario 2: Representative Phase One Media Group & Scheduled Publication Flow."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ports.media import MediaGroup, MediaGroupMember
from telegram_assist_bot.application.ports.publication import PublicationPayload
from telegram_assist_bot.application.publication.publish_immediately import (
    PublishRequest,
)
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    DestinationSelection,
    SelectionMode,
)
from telegram_assist_bot.domain.media.models import (
    MediaIdentity,
    MediaType,
    StoredMedia,
)
from telegram_assist_bot.domain.posts.entities import TelegramEntity
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoScheduleRepository,
    initialize_publication_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

pytestmark = pytest.mark.e2e
_NOW = datetime(2026, 7, 22, 12, 0, 0, tzinfo=UTC)


class FakeUserApiPublisher:
    """Fake Telegram User API publisher for album publication."""

    def __init__(self) -> None:
        self.published_requests: list[PublishRequest] = []

    async def publish(self, request: PublishRequest) -> str:
        self.published_requests.append(request)
        return f"published-album-msg-{len(self.published_requests)}"


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


@async_test
async def test_phase_one_media_schedule_flow(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Execute representative Media Group and scheduled publication flow."""
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        close_mongodb_client,
        create_mongodb_client,
        verify_mongodb_connection,
    )
    from telegram_assist_bot.shared.config import (
        MongoConfig,
        ResolvedSecrets,
        SecretReference,
    )

    # Load album fixture
    fixture_path = (
        Path(__file__).resolve().parents[1]  # noqa: ASYNC240
        / "fixtures"
        / "telegram"
        / "phase_one_album_fixture.json"
    )
    album_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    uri_env = "TEST_MONGODB_URI"
    config_mongo = MongoConfig(
        uri=SecretReference(environment_variable=uri_env),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config_mongo, ResolvedSecrets({uri_env: mongodb_test_settings.uri})
    )

    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        db = client[config_mongo.database_name]

        schedules_col = db["scheduled_publications"]
        queues_col = db["schedule_queues"]
        publications_col = db["publications"]

        await initialize_publication_indexes(
            publications_col, schedules_col, queues_col
        )

        # 1 & 2. Out-of-order message arrival ->
        # normalized into deterministic album order
        # Message 102 arrived first in fixture array!
        channel_id = album_fixture["messages"][0]["source_channel_id"]
        group_id = str(album_fixture["grouped_id"])

        members = []
        for msg_data in album_fixture["messages"]:
            msg_id = msg_data["source_message_id"]
            caption = msg_data.get("caption")
            entities = tuple(
                TelegramEntity(
                    entity_type=e["type"],
                    offset_utf16=e["offset"],
                    length_utf16=e["length"],
                    custom_emoji_id=e.get("custom_emoji_id"),
                )
                for e in msg_data.get("entities", [])
            )
            stored_media = StoredMedia(
                identity=MediaIdentity(channel_id, msg_id, 0),
                media_type=MediaType.PHOTO,
                content_hash="a" * 64,
                size_bytes=msg_data["file_size"],
                mime_type=msg_data.get("mime_type", "image/jpeg"),
                original_filename=f"media-{msg_id}.jpg",
                storage_path=f"var/media/media-{msg_id}.jpg",
                expires_at=_NOW + timedelta(days=14),
            )
            members.append(
                MediaGroupMember(
                    source_message_id=msg_id,
                    source_date=_NOW,
                    media=stored_media,
                    caption=caption,
                    caption_entities=entities,
                    observed_at=_NOW,
                    telegram_group_id=group_id,
                )
            )

        # Sort members deterministically by source_message_id
        sorted_members = tuple(sorted(members, key=lambda m: m.source_message_id))

        media_group = MediaGroup(
            group_key=f"{channel_id}_{group_id}",
            source_channel_id=channel_id,
            telegram_group_id=group_id,
            members=sorted_members,
            first_member_at=_NOW,
            last_member_at=_NOW,
            finalize_after=_NOW + timedelta(seconds=5),
            maximum_wait_until=_NOW + timedelta(seconds=30),
            finalized_at=_NOW,
            observed_message_ids=tuple(m.source_message_id for m in sorted_members),
        )

        post_id_val = f"post-album-{channel_id}-{group_id}"

        # 3 & 4. Verify caption, ZWNJ, Emoji, Custom Emoji and metadata
        primary_member = next(m for m in sorted_members if m.caption is not None)
        assert primary_member.caption is not None
        assert "برنامه‌ریزی" in primary_member.caption
        assert "📸" in primary_member.caption
        assert any(
            e.custom_emoji_id == "98765" for e in primary_member.caption_entities
        )

        # 5 & 6. Delivered as single approval proposal
        assert media_group.group_key == f"{channel_id}_{group_id}"

        # 7 & 8 & 9. Admin selects scheduled publication -> stored in MongoDB
        admin = Administrator(
            telegram_user_id=101,
            active=True,
            role="admin",
            permissions=frozenset([AdminPermission.TOGGLE]),
            allowed_destination_ids=frozenset([-1001999888]),
        )
        selection = DestinationSelection(post_id_val, -1001999888)
        selection = selection.toggle(
            SelectionMode.SCHEDULED,
            actor_id=admin.telegram_user_id,
            occurred_at=_NOW,
            correlation_id="corr-sched-1",
        )
        assert selection.mode == SelectionMode.SCHEDULED

        scheduled_time = _NOW + timedelta(hours=2)

        sched_repo = MongoScheduleRepository(schedules_col, queues_col)
        reservation = await sched_repo.reserve(
            job_id="job-sched-album-1",
            post_id=post_id_val,
            destination_id=-1001999888,
            now=_NOW,
            interval=timedelta(hours=2),
        )
        assert reservation.created is True

        # 10 & 11 & 12. Simulate process shutdown and recovery by a new runtime instance
        await close_mongodb_client(client, timeout_seconds=5)

        # Restart against same database
        client2 = create_mongodb_client(
            config_mongo, ResolvedSecrets({uri_env: mongodb_test_settings.uri})
        )
        await verify_mongodb_connection(client2, timeout_seconds=5)
        db2 = client2[config_mongo.database_name]
        sched_repo2 = MongoScheduleRepository(
            db2["scheduled_publications"], db2["schedule_queues"]
        )

        # 13. Claim due job after scheduled_time arrives
        claimed_job = await sched_repo2.claim_due(
            owner="worker-instance-2",
            now=scheduled_time + timedelta(seconds=1),
            lease_until=scheduled_time + timedelta(seconds=61),
            action="scheduled",
        )
        assert claimed_job is not None
        assert claimed_job.job_id == "job-sched-album-1"

        # 14, 15, 16. Publish album via Fake User API exactly once
        fake_user_api = FakeUserApiPublisher()
        payload = PublicationPayload(
            destination_id=-1001999888,
            text=primary_member.caption,
            entities=primary_member.caption_entities,
        )
        pub_req = PublishRequest(
            post_id=post_id_val,
            destination_id=-1001999888,
            payload=payload,
            owner="worker-instance-2",
            correlation_id="corr-sched-pub-1",
            authorized=True,
            post_publishable=True,
            immediate_selected=False,
            session_valid=True,
            account_premium=True,
            destination_accessible=True,
            action="scheduled",
        )
        pub_id = await fake_user_api.publish(pub_req)
        assert pub_id == "published-album-msg-1"
        assert len(fake_user_api.published_requests) == 1

        # Verify no admin header in published payload caption
        assert pub_req.payload.text is not None
        published_caption = pub_req.payload.text
        assert "📋" not in published_caption
        assert "آلبوم جدید" in published_caption

        # 17 & 18. Repeated claim attempt after completion yields None (no republishing)
        completed = await sched_repo2.complete(
            "job-sched-album-1",
            owner="worker-instance-2",
            at=scheduled_time + timedelta(seconds=2),
        )
        assert completed is True

        no_job = await sched_repo2.claim_due(
            owner="worker-instance-3",
            now=scheduled_time + timedelta(seconds=10),
            lease_until=scheduled_time + timedelta(seconds=70),
            action="scheduled",
        )
        assert no_job is None

    finally:
        if "client2" in locals():
            await close_mongodb_client(client2, timeout_seconds=5)
