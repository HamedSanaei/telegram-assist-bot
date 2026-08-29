"""Verify independent media retention against real MongoDB and filesystem."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol

import pytest

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.domain.posts import POST_RETENTION_PERIOD
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
    MongoDocument,
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

    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection
    from pymongo.asynchronous.database import AsyncDatabase

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


async def _open_repository(
    mongodb_test_settings: MongoTestSettings,
) -> tuple[
    MongoContentPreparationRepository,
    AsyncMongoClient[MongoDocument],
    AsyncCollection[MongoDocument],
    AsyncDatabase[MongoDocument],
    MongoMediaReferenceCollections,
]:
    """Open one isolated MongoDB-backed cleanup boundary for one test."""

    config = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=mongodb_test_settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(
        config, ResolvedSecrets({_URI_ENV: mongodb_test_settings.uri})
    )
    await verify_mongodb_connection(client, timeout_seconds=5)
    database = client[config.database_name]
    media = database["media_items"]
    groups = database["media_groups"]
    preparations = database["content_preparations"]
    await initialize_content_preparation_indexes(media, groups, preparations)
    references = MongoMediaReferenceCollections(
        posts=database["posts"],
        publications=database["publications"],
        schedules=database["scheduled_publications"],
        native_schedules=database["native_schedule_commands"],
        approval_deliveries=database["approval_deliveries"],
        advertisement_sources=database["advertisement_sources"],
        advertisement_slots=database["advertisement_slots"],
    )
    repository = MongoContentPreparationRepository(
        media, groups, preparations, references=references
    )
    return repository, client, media, database, references


def test_media_retention_references_legacy_restart_and_concurrency(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        repository, client, media, database, references = await _open_repository(
            mongodb_test_settings
        )
        try:
            indexes = {item["name"] async for item in await media.list_indexes()}
            assert "ix_media_cleanup_v1" in indexes
            assert "ix_media_retention_cleanup_v2" in indexes
            assert "ix_media_cleanup_deferral_v3" in indexes
            assert "ix_media_post_path_v1" in indexes

            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 7, 27, 12, tzinfo=UTC)

            fresh = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-100, 1),
                content=b"fresh",
                expires_at=now + timedelta(days=1),
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
            await database["publications"].insert_one(
                {
                    "_id": "publication",
                    "post_id": "post-publication",
                    "state": "Pending",
                }
            )
            await database["native_schedule_commands"].insert_one(
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
            await database["approval_deliveries"].insert_one(
                {"_id": "post-approval", "status": "completed"}
            )
            await database["advertisement_sources"].insert_one(
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
                defer_interval=timedelta(hours=1),
            )
            first = await use_case.execute(now=now)
            assert first.deleted == 1
            assert not await storage.exists(expired.storage_path)
            assert await storage.exists(fresh.storage_path)
            shared_document = await media.find_one({"_id": shared_expired.identity.key})
            assert shared_document is not None
            assert shared_document["cleaned_at"] is None
            assert await storage.exists(publication_media.storage_path)
            assert await storage.exists(native_media.storage_path)
            assert await storage.exists(approval_media.storage_path)
            assert await storage.exists(advertisement_media.storage_path)

            await database["publications"].update_one(
                {"_id": "publication"}, {"$set": {"state": "Succeeded"}}
            )
            await database["native_schedule_commands"].update_one(
                {"_id": "native"}, {"$set": {"status": "resolved"}}
            )
            await database["posts"].update_one(
                {"_id": "post-approval"}, {"$set": {"expires_at": now}}
            )
            await database["advertisement_sources"].update_one(
                {"_id": "snapshot"}, {"$set": {"is_current": False}}
            )
            # Deferred candidates are rechecked only after the defer interval.
            later = now + timedelta(hours=1) + timedelta(seconds=1)
            second = await use_case.execute(now=later)
            assert second.deleted == 4

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
            groups = database["media_groups"]
            preparations = database["content_preparations"]
            restarted = MongoContentPreparationRepository(
                media, groups, preparations, references=references
            )
            legacy_result = await CleanupExpiredMedia(
                restarted,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=1,
                defer_interval=timedelta(hours=1),
            ).execute(now=now)
            assert legacy_result.deleted == 1
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
                defer_interval=timedelta(hours=1),
            )
            outcomes = await asyncio.gather(
                worker.execute(now=now), worker.execute(now=now)
            )
            assert sum(item.deleted for item in outcomes) == 1
            assert not await storage.exists(race.storage_path)

            post_received = now - timedelta(days=1)
            assert timedelta(days=14) == POST_RETENTION_PERIOD
            assert post_received + POST_RETENTION_PERIOD == now + timedelta(days=13)
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_cleanup_candidate_starvation_regression(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """The first referenced page can never starve a later unreferenced record."""

    async def scenario() -> None:
        repository, client, media, _database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 7, 28, 12, tzinfo=UTC)
            batch_size = 100
            for index in range(1, batch_size + 1):
                # Four-digit ids keep _id string order identical to numeric order.
                message_id = 1000 + index

                async def chunks() -> AsyncIterator[bytes]:
                    yield b"referenced"

                path, size, digest = await storage.store(
                    MediaIdentity(-200, message_id), chunks(), maximum_bytes=1024
                )
                await repository.save_media_if_absent(
                    StoredMedia(
                        MediaIdentity(-200, message_id),
                        MediaType.DOCUMENT,
                        digest,
                        size,
                        "application/octet-stream",
                        "fixture.bin",
                        path,
                        now,
                    )
                )
                # A fresh sibling sharing the same path keeps this one referenced.
                await repository.save_media_if_absent(
                    StoredMedia(
                        MediaIdentity(-201, message_id),
                        MediaType.DOCUMENT,
                        digest,
                        size,
                        "application/octet-stream",
                        "fixture.bin",
                        path,
                        now + timedelta(days=1),
                    )
                )

            async def free_chunks() -> AsyncIterator[bytes]:
                yield b"free"

            free_path, free_size, free_hash = await storage.store(
                MediaIdentity(-200, 2000), free_chunks(), maximum_bytes=1024
            )
            free_media = await repository.save_media_if_absent(
                StoredMedia(
                    MediaIdentity(-200, 2000),
                    MediaType.DOCUMENT,
                    free_hash,
                    free_size,
                    "application/octet-stream",
                    "fixture.bin",
                    free_path,
                    now,
                )
            )

            use_case = CleanupExpiredMedia(
                repository,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=batch_size,
                defer_interval=timedelta(hours=1),
            )
            first = await use_case.execute(now=now)
            assert first.deleted == 0
            assert first.deferred == batch_size
            assert await storage.exists(free_path)

            second = await use_case.execute(now=now)
            assert second.deleted == 1
            assert second.deferred == 0
            assert not await storage.exists(free_path)
            free_document = await media.find_one({"_id": free_media.identity.key})
            assert free_document is not None
            assert free_document["cleaned_at"] == now

            deferred_document = await media.find_one(
                {"_id": MediaIdentity(-200, 1001).key}
            )
            assert deferred_document is not None
            assert deferred_document["cleanup_next_check_at"] == now + timedelta(
                hours=1
            )
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_filesystem_orphan_scan_uses_grace_and_active_records(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """Orphans older than grace are deleted only when truly unreferenced."""

    async def scenario() -> None:
        repository, client, media, _database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 7, 28, 12, tzinfo=UTC)

            async def old_chunks() -> AsyncIterator[bytes]:
                yield b"old-orphan"

            orphan_path, _orphan_size, _orphan_hash = await storage.store(
                MediaIdentity(-300, 1), old_chunks(), maximum_bytes=1024
            )
            old_stamp = (now - timedelta(hours=2)).timestamp()
            os.utime(tmp_path / orphan_path, (old_stamp, old_stamp))

            tracked = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-300, 2),
                content=b"tracked",
                expires_at=now + timedelta(days=1),
            )
            os.utime(tmp_path / tracked.storage_path, (old_stamp, old_stamp))

            use_case = CleanupExpiredMedia(
                repository,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=10,
                defer_interval=timedelta(hours=1),
            )
            result = await use_case.execute(now=now)
            assert result.orphan_deleted == 1
            assert not (tmp_path / orphan_path).exists()
            assert await storage.exists(tracked.storage_path)
            assert (
                await media.count_documents(
                    {"storage_path": tracked.storage_path, "cleaned_at": None}
                )
                == 1
            )
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
