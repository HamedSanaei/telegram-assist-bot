"""Exercise Milestone 2 persistence, filesystem and concurrency on test MongoDB."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import pytest

from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
from telegram_assist_bot.application.categorize_post import KeywordCategoryRule
from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ports import MediaDownloadSpec, MediaGroupMember
from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparationInput,
    PreparePostPipeline,
)
from telegram_assist_bot.domain.categories import Category
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from telegram_assist_bot.domain.posts import PostId, TelegramEntity
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
    close_mongodb_client,
    create_mongodb_client,
    verify_mongodb_connection,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


class MongoTestSettings(Protocol):
    """Describe the guarded MongoDB test fixture."""

    uri: str
    database_name: str


class Source:
    """Return synthetic Persian media bytes."""

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        del opaque_reference

        async def stream() -> AsyncIterator[bytes]:
            yield "مدیا 😀".encode()

        return stream()


def test_media_album_duplicate_pipeline_and_cleanup(
    mongodb_test_settings: MongoTestSettings, tmp_path: Path
) -> None:
    async def scenario() -> None:
        config = MongoConfig(
            uri=SecretReference(environment_variable=_URI_ENV),
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=5,
        )
        client = create_mongodb_client(
            config, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
        )
        try:
            await verify_mongodb_connection(client, timeout_seconds=5)
            database = client[config.database_name]
            media_collection = database["media_items"]
            groups = database["media_groups"]
            preparations = database["content_preparations"]
            await initialize_content_preparation_indexes(
                media_collection, groups, preparations
            )
            repository = MongoContentPreparationRepository(
                media_collection, groups, preparations
            )
            storage = LocalMediaStorage(tmp_path / "media")
            now = datetime(2026, 1, 20, tzinfo=UTC)
            spec = MediaDownloadSpec(
                MediaIdentity(-100, 1),
                MediaType.PHOTO,
                "opaque",
                "image/jpeg",
                "تصویر 😀.jpg",
                now + timedelta(days=14),
            )
            downloader = DownloadPostMedia(
                Source(), storage, repository, maximum_bytes=1000, timeout_seconds=2
            )
            first, second = await asyncio.gather(
                downloader.execute(spec), downloader.execute(spec)
            )
            assert first == second
            assert await media_collection.count_documents({}) == 1

            assembler = AssembleMediaGroup(
                repository,
                quiet_window=timedelta(seconds=1),
                maximum_wait=timedelta(seconds=5),
            )
            second_media = replace(
                first, identity=MediaIdentity(-100, 2), content_hash="b" * 64
            )
            group = await assembler.add_member(
                source_channel_id=-100,
                telegram_group_id="album",
                member=MediaGroupMember(
                    2,
                    now + timedelta(milliseconds=1),
                    second_media,
                    "کپشن‌",
                    (TelegramEntity(0, 1, "bold"),),
                ),
            )
            group = await assembler.add_member(
                source_channel_id=-100,
                telegram_group_id="album",
                member=MediaGroupMember(
                    1,
                    now,
                    first,
                    "کپشن‌فارسی 😀",
                    (TelegramEntity(11, 2, "custom_emoji", "42"),),
                ),
            )
            group = await assembler.add_member(
                source_channel_id=-100,
                telegram_group_id="album",
                member=group.members[0],
            )
            assert [item.source_message_id for item in group.members] == [1, 2]
            restarted = MongoContentPreparationRepository(
                media_collection, groups, preparations
            )
            assert await restarted.get_group(group.group_key) == group
            outcomes = await asyncio.gather(
                *(
                    assembler.finalize_if_due(
                        group.group_key, now=now + timedelta(seconds=2)
                    )
                    for _ in range(2)
                )
            )
            assert outcomes.count(True) == 1

            pipeline = PreparePostPipeline(repository)
            request = PreparationInput(
                PostId("post-1"),
                "سلام‌ @source_name 😀",
                None,
                (TelegramEntity(19, 2, "custom_emoji", "9"),),
                "source_name",
                (first.content_hash,),
                (Category("news", "اخبار"),),
                (KeywordCategoryRule("r1", "news", "سلام", 1),),
                "news",
                (DestinationSpec("d1", "dest_name"),),
                now,
            )
            results = await asyncio.gather(
                pipeline.execute(request), pipeline.execute(request)
            )
            assert results[0] == results[1]
            assert await preparations.count_documents({"_id": "post-1"}) == 1

            await media_collection.update_one(
                {"_id": first.identity.key}, {"$set": {"expires_at": now}}
            )
            assert (
                await CleanupExpiredMedia(
                    repository, storage, orphan_grace=timedelta(hours=1), batch_size=10
                ).execute(now=now)
                == 1
            )
            assert not await storage.exists(first.storage_path)
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
