"""MongoDB integration proofs for restart-safe approval cleanup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.cleanup_expired_approvals import (
    CleanupExpiredApprovals,
)
from telegram_assist_bot.application.ports import ApprovalDeleteOutcome
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoApprovalCleanupRepository,
    MongoOperationalApprovalRepository,
    initialize_approval_cleanup_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


class RecordingGateway:
    """Record only persisted approval identifiers without Telegram access."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int]] = []

    async def delete_approval_message(
        self, chat_id: int, message_id: int
    ) -> ApprovalDeleteOutcome:
        self.calls.append((chat_id, message_id))
        return ApprovalDeleteOutcome.DELETED


def test_legacy_backfill_boundary_cleanup_and_source_destination_isolation(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            references = database["approval_references"]
            callbacks = database["approval_callbacks"]
            deliveries = database["approval_deliveries"]
            posts = database["posts"]
            media = database["media_items"]
            preparations = database["content_preparations"]
            await initialize_approval_cleanup_indexes(references)
            await initialize_approval_cleanup_indexes(references)
            base = datetime(2026, 7, 25, 12, tzinfo=UTC)
            now = base + timedelta(days=2)
            await deliveries.insert_one(
                {
                    "_id": "post-1",
                    "ready_at": base,
                    "created_at": base,
                    "approval_expired": False,
                }
            )
            await preparations.insert_one({"_id": "post-1", "ready_at": base})
            await references.insert_many(
                [
                    {
                        "_id": "approval:post-1:7",
                        "post_id": "post-1",
                        "actor_id": 7,
                        "chat_id": 7,
                        "header_message_id": 103,
                        "content_message_ids": [101, 102],
                        "active": True,
                    },
                    {
                        "_id": "approval:post-1:8",
                        "post_id": "post-1",
                        "actor_id": 8,
                        "chat_id": 8,
                        "header_message_id": 203,
                        "content_message_ids": [201, 202],
                        "active": True,
                    },
                ]
            )
            await callbacks.insert_one(
                {
                    "_id": "callback",
                    "post_id": "post-1",
                    "issued_at": base,
                    "revoked": False,
                }
            )
            await posts.insert_one(
                {
                    "_id": "post-1",
                    "source_channel_id": -1009001,
                    "source_message_id": 9001,
                    "destination_message_ids": [9002],
                }
            )
            await media.insert_one(
                {
                    "_id": "media-1",
                    "post_id": "post-1",
                    "storage_path": "shared/file.bin",
                    "media_expires_at": now,
                }
            )
            repository = MongoApprovalCleanupRepository(
                references, callbacks, deliveries
            )
            gateway = RecordingGateway()
            cleanup = CleanupExpiredApprovals(
                repository,
                gateway,
                owner="worker",
                clock=lambda: now,
                retention_days=2,
                lease_seconds=30,
                retry_seconds=1,
                max_attempts=2,
            )

            assert await cleanup.execute_batch(batch_size=10) == 2
            assert gateway.calls == [
                (7, 101),
                (7, 102),
                (7, 103),
                (8, 201),
                (8, 202),
                (8, 203),
            ]
            targeted_ids = {message_id for _, message_id in gateway.calls}
            assert targeted_ids.isdisjoint({9001, 9002})
            assert await references.count_documents({"cleanup_state": "deleted"}) == 2
            callback = await callbacks.find_one({"_id": "callback"})
            assert callback is not None
            assert callback["revoked"] is True
            delivery = await deliveries.find_one({"_id": "post-1"})
            assert delivery is not None
            assert delivery["approval_expired"] is True
            operational = MongoOperationalApprovalRepository(preparations, deliveries)
            assert not await operational.is_actionable("post-1")
            assert await media.count_documents({"_id": "media-1"}) == 1
            indexes = await references.index_information()
            assert "ix_approval_cleanup_claim_v1" in indexes
        finally:
            await client.close()

    asyncio.run(scenario())


def test_competing_workers_and_restart_recover_one_expired_lease(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        try:
            database = client[mongodb_test_settings.database_name]
            references = database["approval_references"]
            callbacks = database["approval_callbacks"]
            deliveries = database["approval_deliveries"]
            now = datetime(2026, 7, 27, 12, tzinfo=UTC)
            await references.insert_one(
                {
                    "_id": "approval:restart:7",
                    "post_id": "restart",
                    "actor_id": 7,
                    "chat_id": 7,
                    "header_message_id": 2,
                    "content_message_ids": [1],
                    "active": True,
                    "approval_expires_at": now,
                    "cleanup_state": "pending",
                    "cleanup_next_attempt_at": now,
                }
            )
            repository = MongoApprovalCleanupRepository(
                references, callbacks, deliveries
            )
            claims = await asyncio.gather(
                repository.claim_expired(
                    owner="one",
                    now=now,
                    lease_until=now + timedelta(seconds=30),
                ),
                repository.claim_expired(
                    owner="two",
                    now=now,
                    lease_until=now + timedelta(seconds=30),
                ),
            )
            assert sum(claim is not None for claim in claims) == 1
            winner = next(claim for claim in claims if claim is not None)
            assert await repository.expire_ui(winner, owner="one") or (
                await repository.expire_ui(winner, owner="two")
            )
            assert (
                await repository.claim_expired(
                    owner="restart",
                    now=now + timedelta(seconds=29),
                    lease_until=now + timedelta(seconds=60),
                )
                is None
            )
            recovered = await repository.claim_expired(
                owner="restart",
                now=now + timedelta(seconds=31),
                lease_until=now + timedelta(seconds=61),
            )
            assert recovered is not None
            assert recovered.message_ids == (1, 2)
        finally:
            await client.close()

    asyncio.run(scenario())
