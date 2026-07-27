"""Verify independent media retention against real MongoDB and filesystem."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import pytest

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.domain.posts import POST_RETENTION_PERIOD
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
    close_mongodb_client,
    create_mongodb_client,
    verify_mongodb_connection,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    MongoMediaReferenceCollections,
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
    uri: str
    database_name: str


async def _stored(
    storage: LocalMediaStorage,
    repository: MongoContentPreparationRepository,
    *,
    identity: MediaIdentity,
    content: bytes,
    expires_at: datetime,
    post_id: str | None = None,
) -> StoredMedia:
    async def chunks() -> AsyncIterator[bytes]:
        yield content

    path, size, digest = await storage.store(identity, chunks(), maximum_bytes=1024)
    return await repository.save_media_if_absent(
        StoredMedia(
            identity,
            MediaType.DOCUMENT,
            digest,
            size,
            "application/octet-stream",
            "fixture.bin",
            path,
            expires_at,
            post_id,
        )
    )


def test_media_retention_references_legacy_restart_and_concurrency(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
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
            media = database["media_items"]
            groups = database["media_groups"]
            preparations = database["content_preparations"]
            publications = database["publications"]
            native = database["native_schedule_commands"]
            approval_deliveries = database["approval_deliveries"]
            advertisement_sources = database["advertisement_sources"]
            await initialize_content_preparation_indexes(media, groups, preparations)
            indexes = {item["name"] async for item in await media.list_indexes()}
            assert "ix_media_cleanup_v1" in indexes
            assert "ix_media_retention_cleanup_v2" in indexes
            assert "ix_media_post_path_v1" in indexes

            references = MongoMediaReferenceCollections(
                posts=database["posts"],
                publications=publications,
                schedules=database["scheduled_publications"],
                native_schedules=native,
                approval_deliveries=approval_deliveries,
                advertisement_sources=advertisement_sources,
                advertisement_slots=database["advertisement_slots"],
            )
            repository = MongoContentPreparationRepository(
                media, groups, preparations, references=references
            )
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 7, 27, 12, tzinfo=UTC)

            fresh = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 1),
                content=b"fresh",
                expires_at=now + timedelta(seconds=1),
            )
            expired = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 2),
                content=b"expired",
                expires_at=now,
            )
            shared_expired = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-101, 1),
                content=b"fresh",
                expires_at=now,
            )
            assert shared_expired.storage_path == fresh.storage_path
            publication_media = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 3),
                content=b"publication",
                expires_at=now,
                post_id="post-publication",
            )
            native_media = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 4),
                content=b"native",
                expires_at=now,
                post_id="post-native",
            )
            approval_media = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 8),
                content=b"approval",
                expires_at=now,
                post_id="post-approval",
            )
            advertisement_media = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 5),
                content=b"advertisement",
                expires_at=now,
            )
            await publications.insert_one(
                {
                    "_id": "publication",
                    "post_id": "post-publication",
                    "state": "Pending",
                }
            )
            await native.insert_one(
                {
                    "_id": "native",
                    "post_id": "post-native",
                    "status": "scheduled",
                }
            )
            await database["posts"].insert_one(
                {
                    "_id": "post-approval",
                    "status": "Stored",
                    "expires_at": now + timedelta(days=13),
                }
            )
            await approval_deliveries.insert_one(
                {"_id": "post-approval", "status": "completed"}
            )
            await advertisement_sources.insert_one(
                {
                    "_id": "snapshot",
                    "is_current": True,
                    "media_references": [
                        {"storage_path": advertisement_media.storage_path}
                    ],
                }
            )

            use_case = CleanupExpiredMedia(
                repository,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=100,
            )
            assert await use_case.execute(now=now) == 1
            assert not await storage.exists(expired.storage_path)
            assert await storage.exists(fresh.storage_path)
            shared_document = await media.find_one({"_id": shared_expired.identity.key})
            assert shared_document is not None
            assert shared_document["cleaned_at"] is None
            assert await storage.exists(publication_media.storage_path)
            assert await storage.exists(native_media.storage_path)
            assert await storage.exists(approval_media.storage_path)
            assert await storage.exists(advertisement_media.storage_path)

            await publications.update_one(
                {"_id": "publication"}, {"$set": {"state": "Succeeded"}}
            )
            await native.update_one({"_id": "native"}, {"$set": {"status": "resolved"}})
            await database["posts"].update_one(
                {"_id": "post-approval"}, {"$set": {"expires_at": now}}
            )
            await advertisement_sources.update_one(
                {"_id": "snapshot"}, {"$set": {"is_current": False}}
            )
            assert await use_case.execute(now=now) == 4

            legacy_identity = MediaIdentity(-100, 6)

            async def legacy_chunks() -> AsyncIterator[bytes]:
                yield b"legacy"

            legacy_path, legacy_size, legacy_hash = await storage.store(
                legacy_identity, legacy_chunks(), maximum_bytes=1024
            )
            await media.insert_one(
                {
                    "_id": legacy_identity.key,
                    "source_channel_id": legacy_identity.source_channel_id,
                    "source_message_id": legacy_identity.source_message_id,
                    "item_index": 0,
                    "media_type": MediaType.DOCUMENT.value,
                    "content_hash": legacy_hash,
                    "size_bytes": legacy_size,
                    "mime_type": None,
                    "original_filename": None,
                    "storage_path": legacy_path,
                    "expires_at": now,
                    "cleaned_at": None,
                }
            )
            restarted = MongoContentPreparationRepository(
                media, groups, preparations, references=references
            )
            assert (
                await CleanupExpiredMedia(
                    restarted,
                    storage,
                    orphan_grace=timedelta(hours=1),
                    batch_size=1,
                ).execute(now=now)
                == 1
            )
            legacy_document = await media.find_one({"_id": legacy_identity.key})
            assert legacy_document is not None
            assert "media_expires_at" not in legacy_document
            assert legacy_document["cleaned_at"] == now

            race = await _stored(
                storage,
                restarted,
                identity=MediaIdentity(-100, 7),
                content=b"race",
                expires_at=now,
            )
            worker = CleanupExpiredMedia(
                restarted,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=10,
            )
            outcomes = await asyncio.gather(
                worker.execute(now=now), worker.execute(now=now)
            )
            assert sum(outcomes) == 1
            assert not await storage.exists(race.storage_path)

            post_received = now - timedelta(days=1)
            assert timedelta(days=14) == POST_RETENTION_PERIOD
            assert post_received + POST_RETENTION_PERIOD == now + timedelta(days=13)
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
