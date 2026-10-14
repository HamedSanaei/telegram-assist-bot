"""Real MongoDB regression proofs for expired original approval content."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import pytest
from pymongo import AsyncMongoClient

from telegram_assist_bot.application.ports.operational_approval import (
    ApprovalContentUnavailableError,
    ApprovalPreparationNotReadyError,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoApprovalPostLoader,
    MongoOperationalApprovalRepository,
    initialize_operational_approval_indexes,
)
from tests.unit.application.test_operational_approval import NOW, build_executor

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings


def test_orphan_sync_survives_restart_and_preserves_all_history(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri,
            tz_aware=True,
            timeoutMS=5000,
        )
        try:
            db = client[mongodb_test_settings.database_name]
            preparations, deliveries = (
                db["content_preparations"],
                db["approval_deliveries"],
            )
            await initialize_operational_approval_indexes(deliveries)
            await preparations.insert_one({"_id": "post-1", "ready_at": NOW})
            repository = MongoOperationalApprovalRepository(preparations, deliveries)
            await repository.ensure_delivery("post-1", ready_at=NOW)
            await deliveries.update_one(
                {"_id": "post-1"},
                {
                    "$set": {
                        "status": "completed",
                        "administrator_states": {"7": {"status": "completed"}},
                        "history": [{"action": "published", "at": NOW}],
                    }
                },
            )
            receipt = {
                "_id": "receipt",
                "post_id": "post-1",
                "message_ids": [42],
                "status": "published",
            }
            reference = {
                "_id": "ref",
                "post_id": "post-1",
                "active": True,
                "header_message_id": 9,
            }
            await db["publications"].insert_one(receipt)
            await db["approval_references"].insert_one(reference)
            await repository.record_destination_status(
                "post-1", -1001, status="published", version=1, at=NOW
            )
            before = await deliveries.find_one({"_id": "post-1"})
            assert before is not None
            executor, _, _, _ = build_executor()
            executor._operational = repository
            executor._loader = MongoApprovalPostLoader(
                db["posts"],
                preparations,
                db["media_items"],
                db["media_groups"],
                destination_names=(),
            )
            assert await executor.synchronize_pending_once(
                owner="sync", lease_seconds=30
            )
            after = await deliveries.find_one({"_id": "post-1"})
            assert after is not None
            for key in (
                "status",
                "administrator_states",
                "history",
                "destination_statuses",
            ):
                assert before[key] == after[key]
            assert after["sync_terminal_reason"] == "content_unavailable"
            assert after["sync_terminal_at"] == NOW
            assert after["approval_expired"]
            assert not after["sync_required"]
            assert after["sync_owner"] is None
            assert await db["publications"].find_one({"_id": "receipt"}) == receipt
            assert await db["approval_references"].find_one({"_id": "ref"}) == reference
            assert await preparations.find_one({"_id": "post-1"}) == {
                "_id": "post-1",
                "ready_at": NOW,
            }
            restarted = MongoOperationalApprovalRepository(preparations, deliveries)
            assert not await restarted.is_actionable("post-1")
            assert not await restarted.ensure_delivery("post-1", ready_at=NOW)
            # An independent publisher can still update history without reviving the UI.
            await restarted.record_destination_status(
                "post-1", -1002, status="published", version=2, at=NOW
            )
            assert await restarted.destination_statuses("post-1") == {
                -1001: "published",
                -1002: "published",
            }
            assert (
                await restarted.claim_sync(
                    owner="next",
                    now=NOW + timedelta(days=1),
                    lease_until=NOW + timedelta(days=2),
                )
                is None
            )
            stored = await deliveries.find_one({"_id": "post-1"})
            assert stored is not None
            assert not stored["sync_required"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_terminal_sync_requires_current_owner_version_and_live_lease(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True, timeoutMS=5000
        )
        try:
            db = client[mongodb_test_settings.database_name]
            deliveries = db["approval_deliveries"]
            repository = MongoOperationalApprovalRepository(
                db["content_preparations"], deliveries
            )
            await repository.ensure_delivery("post-1", ready_at=NOW)
            await repository.record_destination_status(
                "post-1", -1001, status="published", version=1, at=NOW
            )
            claim = await repository.claim_sync(
                owner="one", now=NOW, lease_until=NOW + timedelta(seconds=30)
            )
            assert claim is not None
            assert not await repository.terminalize_sync(
                "post-1", owner="other", version=claim.version, at=NOW
            )
            await repository.record_destination_status(
                "post-1", -1002, status="published", version=2, at=NOW
            )
            assert not await repository.terminalize_sync(
                "post-1", owner="one", version=claim.version, at=NOW
            )
            assert not await repository.terminalize_sync(
                "post-1",
                owner="one",
                version=claim.version + 1,
                at=NOW + timedelta(seconds=31),
            )
            replacement = await repository.claim_sync(
                owner="two",
                now=NOW + timedelta(seconds=31),
                lease_until=NOW + timedelta(seconds=61),
            )
            assert replacement is not None
            results = await asyncio.gather(
                *(
                    repository.terminalize_sync(
                        "post-1",
                        owner="two",
                        version=replacement.version,
                        at=NOW + timedelta(seconds=32),
                    )
                    for _ in range(2)
                )
            )
            assert sorted(results) == [False, True]
            assert await repository.destination_statuses("post-1") == {
                -1001: "published",
                -1002: "published",
            }
            # Old documents lacking sync_version still acquire a terminal disposition.
            await deliveries.insert_one({"_id": "legacy", "sync_required": True})
            legacy = await repository.claim_sync(
                owner="legacy", now=NOW, lease_until=NOW + timedelta(seconds=30)
            )
            assert legacy is not None
            assert legacy.version == 0
            assert await repository.terminalize_sync(
                "legacy", owner="legacy", version=0, at=NOW
            )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_loader_distinguishes_missing_original_from_recoverable_preparation(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True, timeoutMS=5000
        )
        try:
            db = client[mongodb_test_settings.database_name]
            loader = MongoApprovalPostLoader(
                db["posts"],
                db["content_preparations"],
                db["media_items"],
                db["media_groups"],
                destination_names=(),
            )
            with pytest.raises(ApprovalContentUnavailableError):
                await loader.load("missing")
            await db["posts"].insert_one({"_id": "recoverable"})
            with pytest.raises(ApprovalPreparationNotReadyError):
                await loader.load("recoverable")
        finally:
            await client.close()

    asyncio.run(scenario())
