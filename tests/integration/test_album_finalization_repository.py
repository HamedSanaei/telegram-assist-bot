"""Verify durable album claims, retries, recovery, and malformed isolation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Protocol

import pytest

from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
from telegram_assist_bot.application.ports import (
    AlbumFinalizationStatus,
    InvalidMediaGroupRecordError,
    MediaGroupMember,
)
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
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

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


class MongoTestSettings(Protocol):
    """Describe the guarded MongoDB test fixture."""

    uri: str
    database_name: str


def test_album_claim_retry_restart_and_legacy_recovery(
    mongodb_test_settings: MongoTestSettings,
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
            await initialize_content_preparation_indexes(media, groups, preparations)
            repository = MongoContentPreparationRepository(media, groups, preparations)
            now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
            assembler = AssembleMediaGroup(
                repository,
                quiet_window=timedelta(seconds=1),
                maximum_wait=timedelta(seconds=5),
            )
            stored = StoredMedia(
                MediaIdentity(-100, 1),
                MediaType.DOCUMENT,
                "a" * 64,
                5,
                "application/octet-stream",
                "safe.bin",
                "sha256/aa/safe",
                now + timedelta(days=1),
            )
            await assembler.observe_member(
                source_channel_id=-100,
                telegram_group_id="album",
                source_message_id=1,
                observed_at=now,
            )
            await assembler.add_member(
                source_channel_id=-100,
                telegram_group_id="album",
                member=MediaGroupMember(
                    1,
                    now - timedelta(days=2),
                    stored,
                    observed_at=now,
                    telegram_group_id="album",
                ),
            )
            due = now + timedelta(seconds=2)
            first, second = await asyncio.gather(
                repository.claim_due_group(
                    now=due,
                    owner="worker-a",
                    lease_until=due + timedelta(seconds=30),
                ),
                repository.claim_due_group(
                    now=due,
                    owner="worker-b",
                    lease_until=due + timedelta(seconds=30),
                ),
            )
            claims = [claim for claim in (first, second) if claim is not None]
            assert len(claims) == 1
            claim = claims[0]
            assert claim.attempt_count == 1
            owner = claim.claim_owner
            assert owner is not None
            retry_at = due + timedelta(seconds=5)
            assert await repository.defer_group_finalization(
                claim.group_key,
                owner=owner,
                next_attempt_at=retry_at,
                failure_category="incomplete_media_group",
            )
            assert (
                await repository.claim_due_group(
                    now=retry_at - timedelta(microseconds=1),
                    owner="worker-c",
                    lease_until=retry_at + timedelta(seconds=30),
                )
                is None
            )
            retried = await repository.claim_due_group(
                now=retry_at,
                owner="worker-c",
                lease_until=retry_at + timedelta(seconds=30),
            )
            assert retried is not None
            assert retried.attempt_count == 2
            assert await repository.complete_group_finalization(
                retried.group_key,
                owner="worker-c",
                at=retry_at,
                canonical_source_message_id=1,
            )
            restarted = MongoContentPreparationRepository(media, groups, preparations)
            assert (
                await restarted.claim_due_group(
                    now=retry_at + timedelta(hours=1),
                    owner="restart",
                    lease_until=retry_at + timedelta(hours=2),
                )
                is None
            )

            legacy_time = retry_at + timedelta(hours=2)
            await groups.insert_one(
                {
                    "_id": "-100:legacy",
                    "source_channel_id": 0,
                    "members": [
                        {
                            "source_message_id": None,
                            "source_date": now,
                            "media": {
                                "source_channel_id": -100,
                                "source_message_id": 9,
                                "item_index": 0,
                                "media_type": "Document",
                                "content_hash": "b" * 64,
                                "size_bytes": 5,
                                "mime_type": None,
                                "original_filename": None,
                                "storage_path": "sha256/bb/legacy",
                                "expires_at": now + timedelta(days=1),
                            },
                            "caption": None,
                            "caption_entities": [],
                        }
                    ],
                    "first_member_at": now,
                    "last_member_at": now,
                    "finalize_after": now,
                    "maximum_wait_until": now + timedelta(seconds=5),
                    "finalized_at": None,
                }
            )
            recovered = await restarted.claim_due_group(
                now=legacy_time,
                owner="legacy-worker",
                lease_until=legacy_time + timedelta(seconds=30),
            )
            assert recovered is not None
            assert recovered.source_channel_id == -100
            assert recovered.telegram_group_id == "legacy"
            assert recovered.members[0].source_message_id == 9
            assert await restarted.complete_group_finalization(
                recovered.group_key,
                owner="legacy-worker",
                at=legacy_time,
                canonical_source_message_id=9,
            )

            await groups.insert_one(
                {
                    "_id": "broken",
                    "source_channel_id": 0,
                    "telegram_group_id": "broken",
                    "members": [],
                    "first_member_at": now,
                    "last_member_at": now,
                    "finalize_after": now,
                    "maximum_wait_until": now + timedelta(seconds=5),
                    "finalized_at": None,
                }
            )
            with pytest.raises(InvalidMediaGroupRecordError) as captured:
                await restarted.claim_due_group(
                    now=legacy_time,
                    owner="broken-worker",
                    lease_until=legacy_time + timedelta(seconds=30),
                )
            assert captured.value.group_key == "broken"
            assert await restarted.fail_group_finalization(
                "broken",
                owner="broken-worker",
                at=legacy_time,
                failure_category="invalid_persisted_group",
            )
            broken = await groups.find_one({"_id": "broken"})
            assert broken is not None
            assert (
                broken["finalization_status"]
                == AlbumFinalizationStatus.PERMANENT_FAILED.value
            )
        finally:
            await close_mongodb_client(client, timeout_seconds=5)

    asyncio.run(scenario())
