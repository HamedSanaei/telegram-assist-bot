"""Verify prepared text, single media, and Album payload reconstruction."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest
from pymongo import AsyncMongoClient

from telegram_assist_bot.application.ports import DestinationArtifact
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import PostId, TelegramEntity
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.publication_payload_loader import (  # noqa: E501
    MongoPublicationPayloadLoader,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def media_document(message_id: int, path: str, now: datetime) -> dict[str, object]:
    """Build sanitized private metadata without binary content."""
    return {
        "_id": f"-100_{message_id}_0",
        "source_channel_id": -100,
        "source_message_id": message_id,
        "item_index": 0,
        "media_type": MediaType.PHOTO.value,
        "content_hash": "a" * 64,
        "size_bytes": 3,
        "mime_type": "image/jpeg",
        "original_filename": None,
        "storage_path": path,
        "expires_at": now + timedelta(days=1),
        "cleaned_at": None,
    }


def test_loads_text_single_media_and_ordered_album(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            media, groups, preparations, posts = (
                database["media_items"],
                database["media_groups"],
                database["content_preparations"],
                database["posts"],
            )
            await initialize_content_preparation_indexes(media, groups, preparations)
            repository = MongoContentPreparationRepository(media, groups, preparations)
            loader = MongoPublicationPayloadLoader(
                repository, posts, media, groups, destination_names={-200: "dest"}
            )
            now = datetime(2026, 7, 12, tzinfo=UTC)
            entity = TelegramEntity(0, 2, "custom_emoji", "123")
            for post_id, message_id in (("text", 1), ("single", 2), ("album", 3)):
                await posts.insert_one(
                    {
                        "_id": post_id,
                        "source_channel_id": -100,
                        "source_message_id": message_id,
                    }
                )
                await repository.save_destination_artifact(
                    DestinationArtifact(PostId(post_id), "dest", "متن🙂", (entity,), 1)
                )
            await media.insert_one(media_document(2, "single.jpg", now))
            first, second = (
                media_document(3, "first.jpg", now),
                media_document(4, "second.jpg", now),
            )
            await groups.insert_one(
                {
                    "_id": "group",
                    "source_channel_id": -100,
                    "telegram_group_id": "g",
                    "finalized_at": now,
                    "members": [
                        {"source_message_id": 3, "media": first},
                        {"source_message_id": 4, "media": second},
                    ],
                }
            )
            text = await loader.load("text", -200)
            single = await loader.load("single", -200)
            album = await loader.load("album", -200)
            assert text.media == ()
            assert [item.storage_path for item in single.media] == ["single.jpg"]
            assert [item.storage_path for item in album.media] == [
                "first.jpg",
                "second.jpg",
            ]
            assert album.text == "متن🙂"
            assert album.entities == (entity,)
        finally:
            await client.close()

    asyncio.run(scenario())


def test_preserves_text_url_metadata_in_prepared_publication_payload(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            repository = MongoContentPreparationRepository(
                database["media_items"],
                database["media_groups"],
                database["content_preparations"],
            )
            loader = MongoPublicationPayloadLoader(
                repository,
                database["posts"],
                database["media_items"],
                database["media_groups"],
                destination_names={-200: "dest"},
            )
            entity = TelegramEntity(
                8, 4, "text_url", url="https://example.invalid/path"
            )
            await database["posts"].insert_one(
                {"_id": "text-url", "source_channel_id": -100, "source_message_id": 9}
            )
            await repository.save_destination_artifact(
                DestinationArtifact(
                    PostId("text-url"), "dest", "سلام 👋 لینک", (entity,), 1
                )
            )

            payload = await loader.load("text-url", -200)

            assert payload.entities == (entity,)
            assert payload.entities[0].url == "https://example.invalid/path"
        finally:
            await client.close()

    asyncio.run(scenario())


def test_rejects_unknown_destination_missing_artifact_and_missing_post(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            repository = MongoContentPreparationRepository(
                database["media_items"],
                database["media_groups"],
                database["content_preparations"],
            )
            loader = MongoPublicationPayloadLoader(
                repository,
                database["posts"],
                database["media_items"],
                database["media_groups"],
                destination_names={-200: "dest"},
            )
            with pytest.raises(ValueError, match="not configured"):
                await loader.load("missing", -201)
            with pytest.raises(ValueError, match="artifact"):
                await loader.load("missing", -200)
            await repository.save_destination_artifact(
                DestinationArtifact(PostId("missing"), "dest", "text", (), 1)
            )
            with pytest.raises(ValueError, match="Post"):
                await loader.load("missing", -200)
        finally:
            await client.close()

    asyncio.run(scenario())
