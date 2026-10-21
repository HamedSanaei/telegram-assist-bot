"""Prove recovery records cannot pin completed album media or outlive source TTL."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import pytest

from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    content_repository,
    create_mongodb_client,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)
from tests.unit.application.test_runtime_ingestion import message

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from pymongo.asynchronous.database import AsyncDatabase

    from tests.integration.test_content_preparation_pipeline import MongoTestSettings

pytestmark = pytest.mark.integration


@asynccontextmanager
async def repository_for(
    settings: MongoTestSettings,
) -> AsyncIterator[
    tuple[
        content_repository.MongoContentPreparationRepository,
        AsyncDatabase[dict[str, Any]],
    ]
]:
    config = MongoConfig(
        uri=SecretReference(environment_variable="TEST_MONGODB_URI"),
        database_name=settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config, ResolvedSecrets({"TEST_MONGODB_URI": settings.uri})
    )
    try:
        database = client[settings.database_name]
        media, groups, preparations = (
            database["media_items"],
            database["media_groups"],
            database["content_preparations"],
        )
        await content_repository.initialize_content_preparation_indexes(
            media, groups, preparations
        )
        yield (
            content_repository.MongoContentPreparationRepository(
                media, groups, preparations
            ),
            database,
        )
    finally:
        await client.close()


def test_acknowledged_non_anchor_album_member_does_not_pin_expired_media(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        async with repository_for(mongodb_test_settings) as (repository, database):
            post_id = PostId("non-anchor-album-member")
            media = StoredMedia(
                MediaIdentity(-100, 2),
                MediaType.PHOTO,
                "a" * 64,
                5,
                "image/jpeg",
                "synthetic.jpg",
                "sha256/aa/" + "a" * 64,
                now + timedelta(days=2),
                post_id.value,
            )
            await repository.save_media_if_absent(media)
            assert await repository.claim_ingestion(
                post_id, owner="worker", now=now, lease_until=now + timedelta(minutes=5)
            )
            # No ready_at exists on a non-anchor member; only the canonical
            # album anchor becomes a ready candidate for approval.
            await repository.acknowledge_ingestion(post_id)
            await repository.release_ingestion(post_id, owner="worker")
            await database["media_groups"].insert_one(
                {
                    "_id": "-100:album",
                    "source_channel_id": -100,
                    "members": [{"media": {"storage_path": media.storage_path}}],
                    "finalization_status": "completed",
                    "finalized_at": now,
                }
            )
            assert not await repository.is_storage_path_referenced(
                media.storage_path, now=now + timedelta(days=3)
            )
            # Unacknowledged processing is still a live cleanup reference.
            await database["content_preparations"].update_one(
                {"_id": post_id.value}, {"$unset": {"ingestion_completed": ""}}
            )
            assert await repository.is_storage_path_referenced(
                media.storage_path, now=now + timedelta(days=3)
            )

    asyncio.run(scenario())


def test_snapshot_ttl_is_exact_and_does_not_expire_preparation_history(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        now = datetime.now(UTC)
        expires_at = now + timedelta(days=14)
        async with repository_for(mongodb_test_settings) as (repository, database):
            post_id = PostId("snapshot-expiration")
            assert await repository.claim_ingestion(
                post_id, owner="worker", now=now, lease_until=now + timedelta(minutes=5)
            )
            fixture = message(
                1, now, media_type=MediaType.DOCUMENT, caption="متن‌اصلی 😀"
            )
            await repository.save_ingestion_message(
                post_id, fixture, expires_at=expires_at
            )
            await repository.release_ingestion(post_id, owner="worker")
            preparations = database["content_preparations"]
            snapshot = await preparations.find_one(
                {"_id": f"ingestion-message:{post_id.value}"}
            )
            state = await preparations.find_one({"_id": post_id.value})
            assert snapshot is not None
            assert state is not None
            assert snapshot["ingestion_snapshot_expires_at"] == expires_at.replace(
                microsecond=expires_at.microsecond // 1000 * 1000
            )
            assert "ingestion_snapshot_expires_at" not in state
            assert len(await repository.list_pending_ingestion(now=now, limit=10)) == 1
            assert (
                await repository.list_pending_ingestion(now=expires_at, limit=10) == ()
            )
            state = await preparations.find_one({"_id": post_id.value})
            assert state is not None
            assert state["recovery_requested"] is False
            indexes = await preparations.index_information()
            ttl = [
                value
                for value in indexes.values()
                if value.get("key") == [("ingestion_snapshot_expires_at", 1)]
            ]
            assert len(ttl) == 1
            assert ttl[0]["expireAfterSeconds"] == 0

    asyncio.run(scenario())
