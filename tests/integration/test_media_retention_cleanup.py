"""Verify independent media retention against real MongoDB and filesystem."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast

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
    due_retry_candidate_filter,
    initialize_content_preparation_indexes,
    never_attempted_candidate_filter,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)
from telegram_assist_bot.workers.media_cleanup import PeriodicMediaCleanupWorker

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from typing import Any

    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection
    from pymongo.asynchronous.database import AsyncDatabase

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"
# Sentinel for approval documents written before `approval_expired` existed.
_LEGACY_APPROVAL = "legacy-missing-expiration"


class _MutableClock:
    """Expose one controllable UTC instant to the periodic cleanup worker."""

    def __init__(self, value: datetime) -> None:
        self.value = value

    def utc_now(self) -> datetime:
        """Return the currently configured synthetic instant."""
        return self.value


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
            assert "ix_media_cleanup_fairness_v4" in indexes
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


async def _synthetic_media(
    repository: MongoContentPreparationRepository,
    identity: MediaIdentity,
    *,
    digest: str,
    expires_at: datetime,
) -> StoredMedia:
    """Persist one metadata-only media record without touching the filesystem."""
    return await repository.save_media_if_absent(
        StoredMedia(
            identity,
            MediaType.DOCUMENT,
            digest,
            len(digest),
            "application/octet-stream",
            "fixture.bin",
            f"sha256/{digest[:2]}/{digest}",
            expires_at,
        )
    )


def test_cleanup_candidate_ordering_is_missing_first_then_due(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Prove the fairness sort against real MongoDB ordering semantics.

    A never-attempted document (absent `cleanup_next_check_at`) and an explicit
    null must both sort before every deferred date, and deferred documents must be
    ordered by their due instant, with `_id` breaking ties deterministically.
    """

    async def scenario() -> None:
        repository, client, media, _database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            now = datetime(2026, 8, 1, 12, tzinfo=UTC)
            future_retry = await _synthetic_media(
                repository, MediaIdentity(-300, 1), digest="1" * 64, expires_at=now
            )
            due_earlier = await _synthetic_media(
                repository, MediaIdentity(-300, 2), digest="2" * 64, expires_at=now
            )
            due_later = await _synthetic_media(
                repository, MediaIdentity(-300, 3), digest="3" * 64, expires_at=now
            )
            never_attempted = await _synthetic_media(
                repository, MediaIdentity(-300, 4), digest="4" * 64, expires_at=now
            )
            explicit_null = await _synthetic_media(
                repository, MediaIdentity(-300, 5), digest="5" * 64, expires_at=now
            )
            await _synthetic_media(
                repository,
                MediaIdentity(-300, 6),
                digest="6" * 64,
                expires_at=now + timedelta(days=1),
            )
            await media.update_one(
                {"_id": future_retry.identity.key},
                {"$set": {"cleanup_next_check_at": now + timedelta(hours=1)}},
            )
            await media.update_one(
                {"_id": due_earlier.identity.key},
                {"$set": {"cleanup_next_check_at": now - timedelta(hours=2)}},
            )
            await media.update_one(
                {"_id": due_later.identity.key},
                {"$set": {"cleanup_next_check_at": now - timedelta(hours=1)}},
            )
            await media.update_one(
                {"_id": explicit_null.identity.key},
                {"$set": {"cleanup_next_check_at": None}},
            )

            candidates = await repository.list_cleanup_candidates(
                now=now, orphan_before=now - timedelta(hours=1), limit=10
            )
            keys = [item.identity.key for item in candidates]
            assert keys == [
                never_attempted.identity.key,
                explicit_null.identity.key,
                due_earlier.identity.key,
                due_later.identity.key,
            ]
            # The two candidate classes must stay disjoint: a due retry can never
            # be selected twice, and a future retry is selected by neither class.
            assert len(set(keys)) == len(keys)
            assert future_retry.identity.key not in keys
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_due_retries_run_after_the_never_attempted_queue_drains(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Class priority, due-retry progress and future-deferral exclusion."""

    async def scenario() -> None:
        repository, client, media, _database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            now = datetime(2026, 8, 9, 12, tzinfo=UTC)
            never_attempted = await _synthetic_media(
                repository, MediaIdentity(-500, 1), digest="1" * 64, expires_at=now
            )
            due_retry = await _synthetic_media(
                repository, MediaIdentity(-500, 2), digest="2" * 64, expires_at=now
            )
            future_retry = await _synthetic_media(
                repository, MediaIdentity(-500, 3), digest="3" * 64, expires_at=now
            )
            await media.update_one(
                {"_id": due_retry.identity.key},
                {"$set": {"cleanup_next_check_at": now - timedelta(hours=1)}},
            )
            await media.update_one(
                {"_id": future_retry.identity.key},
                {"$set": {"cleanup_next_check_at": now + timedelta(hours=1)}},
            )

            first = await repository.list_cleanup_candidates(
                now=now, orphan_before=now - timedelta(hours=1), limit=2
            )
            # A never-attempted candidate precedes an already due retry.
            assert [item.identity.key for item in first] == [
                never_attempted.identity.key,
                due_retry.identity.key,
            ]

            assert await repository.mark_media_cleaned(
                never_attempted.identity, cleaned_at=now
            )
            second = await repository.list_cleanup_candidates(
                now=now, orphan_before=now - timedelta(hours=1), limit=2
            )
            # A not-yet-due retry is never selected, even when it is the only
            # remaining record of its storage path.
            assert [item.identity.key for item in second] == [due_retry.identity.key]

            assert await repository.defer_media_cleanup(
                due_retry.identity, until=now + timedelta(hours=2)
            )
            third = await repository.list_cleanup_candidates(
                now=now, orphan_before=now - timedelta(hours=1), limit=2
            )
            assert third == ()
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def _stage_names(plan: object) -> list[str]:
    """Flatten the stage names of one MongoDB explain plan tree."""
    names: list[str] = []
    stack = [plan]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            stage = node.get("stage")
            if isinstance(stage, str):
                names.append(stage)
            stack.extend(
                value for value in node.values() if isinstance(value, dict | list)
            )
        elif isinstance(node, list):
            stack.extend(node)
    return names


def _execution_stats(explain: dict[str, Any]) -> dict[str, Any]:
    stats = explain["executionStats"]
    assert isinstance(stats, dict)
    return cast("dict[str, Any]", stats)


def _winning_plan(explain: dict[str, Any]) -> dict[str, Any]:
    planner = explain["queryPlanner"]
    assert isinstance(planner, dict)
    plan = planner["winningPlan"]
    assert isinstance(plan, dict)
    return cast("dict[str, Any]", plan)


def test_candidate_selection_stays_bounded_with_large_fresh_population(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    """Production-shaped scale: expired selection must ignore fresh media.

    The collection holds 100k fresh unexpired records (the population that grows
    without bound in production), 1k low-identity expired deferred blockers, 5k
    expired never-attempted records and legacy records that only carry
    `expires_at`. Candidate selection and both candidate queries must stay bounded
    by the expired population instead of walking every stored record.
    """

    async def scenario() -> None:
        repository, client, media, database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            now = datetime(2026, 9, 17, 12, tzinfo=UTC)
            fresh_count = 100_000
            never_count = 5_000
            retry_count = 1_000
            legacy_count = 200
            fresh_expiry = now + timedelta(hours=20)
            expired = now - timedelta(hours=1)

            def document(
                identity: MediaIdentity,
                *,
                expiry: datetime,
                deferred_at: datetime | None = None,
                legacy: bool = False,
            ) -> MongoDocument:
                digest = f"{abs(hash(identity.key)):064d}"
                payload: MongoDocument = {
                    "_id": identity.key,
                    "source_channel_id": identity.source_channel_id,
                    "source_message_id": identity.source_message_id,
                    "item_index": identity.item_index,
                    "media_type": MediaType.DOCUMENT.value,
                    "content_hash": digest,
                    "size_bytes": 1,
                    "mime_type": None,
                    "original_filename": None,
                    "storage_path": f"sha256/ff/{digest}",
                    "expires_at": expiry,
                    "cleaned_at": None,
                }
                if not legacy:
                    payload["media_expires_at"] = expiry
                if deferred_at is not None:
                    payload["cleanup_next_check_at"] = deferred_at
                return payload

            for start in range(0, fresh_count, 10_000):
                await media.insert_many(
                    [
                        document(
                            MediaIdentity(-100, 1_000_000 + index),
                            expiry=fresh_expiry,
                        )
                        for index in range(start, start + 10_000)
                    ]
                )
            await media.insert_many(
                [
                    document(
                        MediaIdentity(-200, 100_000 + index),
                        expiry=expired,
                        deferred_at=now - timedelta(minutes=30),
                    )
                    for index in range(retry_count)
                ]
                + [
                    document(MediaIdentity(-200, 900_000 + index), expiry=expired)
                    for index in range(never_count)
                ]
                + [
                    document(
                        MediaIdentity(-300, 1 + index), expiry=expired, legacy=True
                    )
                    for index in range(legacy_count)
                ]
            )
            stored = await media.count_documents({})
            expired_population = never_count + retry_count + legacy_count
            assert stored == fresh_count + expired_population

            candidates = await repository.list_cleanup_candidates(
                now=now, orphan_before=now - timedelta(hours=1), limit=100
            )
            assert [item.identity.key for item in candidates] == [
                MediaIdentity(-200, 900_000 + index).key for index in range(100)
            ]

            never_attempted_explain = await database.command(
                "explain",
                {
                    "find": "media_items",
                    "filter": never_attempted_candidate_filter(now),
                    "sort": {"_id": 1},
                    "limit": 100,
                },
                verbosity="executionStats",
            )
            due_retry_explain = await database.command(
                "explain",
                {
                    "find": "media_items",
                    "filter": due_retry_candidate_filter(now),
                    "sort": {"cleanup_next_check_at": 1, "_id": 1},
                    "limit": 100,
                },
                verbosity="executionStats",
            )

            never_attempted_stages = _stage_names(
                _winning_plan(never_attempted_explain)
            )
            assert "IXSCAN" in never_attempted_stages
            assert "COLLSCAN" not in never_attempted_stages
            never_attempted_stats = _execution_stats(never_attempted_explain)
            assert never_attempted_stats["nReturned"] == 100
            # Bounded by the expired population, never by the fresh population.
            bounded_keys = expired_population + 200
            assert never_attempted_stats["totalKeysExamined"] <= bounded_keys
            assert never_attempted_stats["totalKeysExamined"] < stored // 10

            due_retry_stages = _stage_names(_winning_plan(due_retry_explain))
            assert "IXSCAN" in due_retry_stages
            assert "COLLSCAN" not in due_retry_stages
            assert "SORT" not in due_retry_stages
            due_retry_stats = _execution_stats(due_retry_explain)
            assert due_retry_stats["nReturned"] == 100
            assert due_retry_stats["totalKeysExamined"] <= retry_count
            assert due_retry_stats["totalDocsExamined"] <= retry_count
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_bounded_worker_cycles_cannot_starve_never_attempted_candidates(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """Deferred blockers must not starve later expired unreferenced media.

    More blockers than a single bounded worker cycle can process are placed before
    the deletable records. After the defer interval elapses the previously
    deferred blockers become eligible again, and the bounded cyclic worker must
    still reach the never-attempted deletable records.
    """

    async def scenario() -> None:
        repository, client, media, _database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 8, 3, 12, tzinfo=UTC)
            batch_size = 100
            max_batches_per_cycle = 3
            blocker_count = 400
            free_count = 50
            defer_interval = timedelta(hours=1)

            for index in range(1, blocker_count + 1):
                content = f"blocked-{index:04d}".encode()
                blocker = await _stored(
                    storage,
                    repository,
                    identity=MediaIdentity(-200, 1000 + index),
                    content=content,
                    expires_at=now,
                )
                # A fresh sibling on the same storage path keeps this record alive.
                sibling = await _stored(
                    storage,
                    repository,
                    identity=MediaIdentity(-201, 1000 + index),
                    content=content,
                    expires_at=now + timedelta(days=1),
                )
                assert sibling.storage_path == blocker.storage_path

            free_keys: list[str] = []
            free_paths: list[str] = []
            for index in range(1, free_count + 1):
                free = await _stored(
                    storage,
                    repository,
                    identity=MediaIdentity(-200, 5000 + index),
                    content=f"free-{index:04d}".encode(),
                    expires_at=now,
                )
                free_keys.append(free.identity.key)
                free_paths.append(free.storage_path)

            use_case = CleanupExpiredMedia(
                repository,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=batch_size,
                defer_interval=defer_interval,
            )
            clock = _MutableClock(now)
            worker = PeriodicMediaCleanupWorker(
                use_case,
                clock,
                interval_seconds=3600,
                max_batches_per_cycle=max_batches_per_cycle,
            )
            stop = asyncio.Event()

            processed_per_cycle = batch_size * max_batches_per_cycle
            assert await worker._drain_cycle(stop) == max_batches_per_cycle
            assert (
                await media.count_documents(
                    {"cleanup_next_check_at": now + defer_interval}
                )
                == processed_per_cycle
            )
            assert await media.count_documents({"cleaned_at": {"$ne": None}}) == 0
            for path in free_paths:
                assert await storage.exists(path)

            later = now + defer_interval + timedelta(seconds=1)
            eligible = await repository.list_cleanup_candidates(
                now=later,
                orphan_before=later - timedelta(hours=1),
                limit=blocker_count + free_count,
            )
            never_attempted_keys = [
                f"-200_{1000 + index}_0"
                for index in range(processed_per_cycle + 1, blocker_count + 1)
            ] + [f"-200_{5000 + index}_0" for index in range(1, free_count + 1)]
            retried_keys = [
                f"-200_{1000 + index}_0" for index in range(1, processed_per_cycle + 1)
            ]
            second_cycle_order = [item.identity.key for item in eligible]

            # The returning deferred blockers still consume part of cycle two, but
            # the never-attempted deletable records are reached inside the bound.
            clock.value = later
            assert await worker._drain_cycle(stop) == max_batches_per_cycle
            assert (
                await media.count_documents(
                    {"_id": {"$in": free_keys}, "cleaned_at": later}
                )
                == free_count
            )
            for path in free_paths:
                assert not await storage.exists(path)
            # Every blocker record and every fresh sibling stayed uncleaned.
            uncleaned = await media.count_documents({"cleaned_at": None})
            assert uncleaned == blocker_count * 2
            previously_never_attempted = await media.find_one({"_id": "-200_1400_0"})
            assert previously_never_attempted is not None
            assert previously_never_attempted["cleanup_next_check_at"] == (
                later + defer_interval
            )
            # Deterministic two-class ordering is asserted after the starvation
            # behavior so a legacy regression fails on the starvation proof first.
            assert second_cycle_order == [*never_attempted_keys, *retried_keys]
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


async def _post_record(
    database: AsyncDatabase[MongoDocument], post_id: str, *, expires_at: datetime
) -> None:
    """Persist one Post document with an explicit retention deadline."""
    await database["posts"].insert_one(
        {"_id": post_id, "status": "Stored", "expires_at": expires_at}
    )


def _active_post_expiry(now: datetime) -> datetime:
    """Return a Post deadline that keeps the Post inside its 14-day window."""
    return now + timedelta(days=13)


@pytest.mark.parametrize(
    ("status", "expiration", "expected_referenced"),
    [
        ("completed", True, False),
        ("completed", False, True),
        ("completed", _LEGACY_APPROVAL, True),
        ("pending", True, True),
        ("claimed", True, True),
        ("retry", True, True),
    ],
    ids=[
        "completed-approval-expired",
        "completed-approval-still-live",
        "completed-approval-legacy-missing",
        "pending-approval-still-protects",
        "claimed-approval-still-protects",
        "retry-approval-still-protects",
    ],
)
def test_approval_reference_respects_explicit_approval_expiration(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
    status: str,
    expiration: object,
    expected_referenced: bool,
) -> None:
    """Only a completed approval whose own lifecycle is live keeps media."""

    async def scenario() -> None:
        repository, client, _media, database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 8, 4, 12, tzinfo=UTC)
            media_item = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-400, 1),
                content=b"approval-media",
                expires_at=now,
                post_id="post-approval",
            )
            await _post_record(
                database, "post-approval", expires_at=_active_post_expiry(now)
            )
            approval: dict[str, object] = {"_id": "post-approval", "status": status}
            if expiration is not _LEGACY_APPROVAL:
                approval["approval_expired"] = expiration
            await database["approval_deliveries"].insert_one(approval)

            referenced = await repository.is_storage_path_referenced(
                media_item.storage_path, now=now
            )
            assert referenced is expected_referenced
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_expired_approval_still_protected_by_publication_or_schedule(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """Publication and Schedule references keep media after approval expiry."""

    async def scenario() -> None:
        repository, client, _media, database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 8, 5, 12, tzinfo=UTC)
            publication_media = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-402, 1),
                content=b"publication-media",
                expires_at=now,
                post_id="post-publication",
            )
            schedule_media = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-402, 2),
                content=b"schedule-media",
                expires_at=now,
                post_id="post-schedule",
            )
            await _post_record(
                database, "post-publication", expires_at=_active_post_expiry(now)
            )
            await _post_record(
                database, "post-schedule", expires_at=_active_post_expiry(now)
            )
            await database["approval_deliveries"].insert_many(
                [
                    {
                        "_id": "post-publication",
                        "status": "completed",
                        "approval_expired": True,
                    },
                    {
                        "_id": "post-schedule",
                        "status": "completed",
                        "approval_expired": True,
                    },
                ]
            )
            await database["publications"].insert_one(
                {
                    "_id": "publication",
                    "post_id": "post-publication",
                    "state": "Pending",
                }
            )
            await database["scheduled_publications"].insert_one(
                {"_id": "schedule", "post_id": "post-schedule", "status": "Pending"}
            )

            assert await repository.is_storage_path_referenced(
                publication_media.storage_path, now=now
            )
            assert await repository.is_storage_path_referenced(
                schedule_media.storage_path, now=now
            )
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_fresh_shared_media_keeps_path_alive_after_approval_expiration(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """A fresh sibling record still protects the shared storage path."""

    async def scenario() -> None:
        repository, client, _media, database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 8, 6, 12, tzinfo=UTC)
            expired = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-403, 1),
                content=b"shared-media",
                expires_at=now,
                post_id="post-shared",
            )
            fresh = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-403, 2),
                content=b"shared-media",
                expires_at=now + timedelta(days=1),
            )
            assert fresh.storage_path == expired.storage_path
            await _post_record(
                database, "post-shared", expires_at=_active_post_expiry(now)
            )
            await database["approval_deliveries"].insert_one(
                {
                    "_id": "post-shared",
                    "status": "completed",
                    "approval_expired": True,
                }
            )

            assert await repository.is_storage_path_referenced(
                expired.storage_path, now=now
            )
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_completed_approval_stops_protecting_after_post_retention(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """The active-Post guard of the completed-approval check is retained."""

    async def scenario() -> None:
        repository, client, _media, database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 8, 7, 12, tzinfo=UTC)
            media_item = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-404, 1),
                content=b"post-retention-media",
                expires_at=now,
                post_id="post-expired",
            )
            await _post_record(
                database,
                "post-expired",
                expires_at=now - timedelta(seconds=1),
            )
            await database["approval_deliveries"].insert_one(
                {"_id": "post-expired", "status": "completed"}
            )

            assert not await repository.is_storage_path_referenced(
                media_item.storage_path, now=now
            )
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())


def test_cleanup_releases_media_only_after_its_approval_expired(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    """The production regression: an expired approval must not pin media."""

    async def scenario() -> None:
        repository, client, _media, database, _references = await _open_repository(
            mongodb_test_settings
        )
        try:
            storage = LocalMediaStorage(tmp_path)
            now = datetime(2026, 8, 8, 12, tzinfo=UTC)
            expired_approval = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-401, 1),
                content=b"expired-approval-media",
                expires_at=now,
                post_id="post-expired-approval",
            )
            live_approval = await _stored(
                storage,
                repository,
                identity=MediaIdentity(-401, 2),
                content=b"live-approval-media",
                expires_at=now,
                post_id="post-live-approval",
            )
            for post_id in ("post-expired-approval", "post-live-approval"):
                await _post_record(
                    database, post_id, expires_at=_active_post_expiry(now)
                )
            await database["approval_deliveries"].insert_many(
                [
                    {
                        "_id": "post-expired-approval",
                        "status": "completed",
                        "approval_expired": True,
                    },
                    {
                        "_id": "post-live-approval",
                        "status": "completed",
                        "approval_expired": False,
                    },
                ]
            )

            result = await CleanupExpiredMedia(
                repository,
                storage,
                orphan_grace=timedelta(hours=1),
                batch_size=10,
                defer_interval=timedelta(hours=1),
            ).execute(now=now)

            assert result.scanned == 2
            assert result.deleted == 1
            assert not await storage.exists(expired_approval.storage_path)
            assert await storage.exists(live_approval.storage_path)
            assert result.deferred == 1
            assert result.reference_deferred == 1
            assert result.failed == 0
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
