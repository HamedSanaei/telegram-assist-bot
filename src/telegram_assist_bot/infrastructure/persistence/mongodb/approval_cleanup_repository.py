"""MongoDB claims and progress for expired approval-message cleanup."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReturnDocument

from telegram_assist_bot.application.ports import ApprovalCleanupClaim

if TYPE_CHECKING:
    from datetime import datetime

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


async def initialize_approval_cleanup_indexes(
    references: AsyncCollection[Document],
) -> None:
    """Create the additive, idempotent approval-expiration claim index."""
    await references.create_index(
        [
            ("approval_expires_at", ASCENDING),
            ("cleanup_state", ASCENDING),
            ("cleanup_next_attempt_at", ASCENDING),
            ("cleanup_lease_until", ASCENDING),
            ("_id", ASCENDING),
        ],
        name="ix_approval_cleanup_claim_v1",
    )


def _claim(document: Document) -> ApprovalCleanupClaim:
    content = tuple(int(value) for value in document.get("content_message_ids", ()))
    header = int(document.get("header_message_id", 0) or 0)
    message_ids = tuple(dict.fromkeys((*content, *((header,) if header > 0 else ()))))
    return ApprovalCleanupClaim(
        reference_id=str(document["_id"]),
        post_id=str(document["post_id"]),
        actor_id=int(document["actor_id"]),
        chat_id=int(document["chat_id"]),
        message_ids=message_ids,
        deleted_message_ids=frozenset(
            int(value) for value in document.get("cleanup_deleted_message_ids", ())
        ),
        expires_at=document["approval_expires_at"],
    )


class MongoApprovalCleanupRepository:
    """Persist cleanup truth without exposing MongoDB details to Application."""

    def __init__(
        self,
        references: AsyncCollection[Document],
        callbacks: AsyncCollection[Document],
        deliveries: AsyncCollection[Document],
    ) -> None:
        """Store concrete collections used by the cleanup transaction boundary."""
        self._references = references
        self._callbacks = callbacks
        self._deliveries = deliveries

    async def backfill_legacy_expirations(
        self, *, now: datetime, retention_days: int, limit: int
    ) -> int:
        """Backfill only a bounded legacy set, safely delaying unknown records."""
        cursor = (
            self._references.find(
                {"approval_expires_at": {"$exists": False}},
                projection={"_id": 1, "post_id": 1},
            )
            .sort("_id", ASCENDING)
            .limit(limit)
        )
        updated = 0
        async for reference in cursor:
            post_id = str(reference["post_id"])
            delivery = await self._deliveries.find_one(
                {"_id": post_id}, projection={"ready_at": 1, "created_at": 1}
            )
            base = None
            if delivery is not None:
                base = delivery.get("ready_at") or delivery.get("created_at")
            if base is None:
                callback = await self._callbacks.find_one(
                    {"post_id": post_id},
                    projection={"issued_at": 1},
                    sort=[("issued_at", ASCENDING)],
                )
                if callback is not None:
                    base = callback.get("issued_at")
            if base is None:
                base = now
            expires_at = base + timedelta(days=retention_days)
            result = await self._references.update_one(
                {"_id": reference["_id"], "approval_expires_at": {"$exists": False}},
                {
                    "$set": {
                        "approval_expires_at": expires_at,
                        "cleanup_state": "pending",
                        "cleanup_next_attempt_at": expires_at,
                    }
                },
            )
            updated += result.modified_count
        return updated

    async def claim_expired(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> ApprovalCleanupClaim | None:
        """Atomically lease one due pending, retry, or abandoned reference."""
        document = await self._references.find_one_and_update(
            {
                "approval_expires_at": {"$lte": now},
                "cleanup_state": {"$nin": ["deleted", "unavailable"]},
                "$and": [
                    {
                        "$or": [
                            {"cleanup_next_attempt_at": {"$exists": False}},
                            {"cleanup_next_attempt_at": None},
                            {"cleanup_next_attempt_at": {"$lte": now}},
                        ]
                    },
                    {
                        "$or": [
                            {"cleanup_lease_until": {"$exists": False}},
                            {"cleanup_lease_until": None},
                            {"cleanup_lease_until": {"$lte": now}},
                        ]
                    },
                ],
            },
            {
                "$set": {
                    "cleanup_state": "claimed",
                    "cleanup_owner": owner,
                    "cleanup_lease_until": lease_until,
                    "cleanup_next_attempt_at": None,
                },
                "$inc": {"cleanup_attempt_count": 1},
            },
            sort=[("approval_expires_at", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else _claim(document)

    async def expire_ui(self, claim: ApprovalCleanupClaim, *, owner: str) -> bool:
        """Disable the durable action gate and callbacks before Bot deletion."""
        current = await self._references.find_one(
            {
                "_id": claim.reference_id,
                "cleanup_state": "claimed",
                "cleanup_owner": owner,
            },
            projection={"post_id": 1},
        )
        if current is None:
            return False
        await self._deliveries.update_one(
            {"_id": claim.post_id}, {"$set": {"approval_expired": True}}
        )
        await self._callbacks.update_many(
            {"post_id": claim.post_id, "revoked": False},
            {"$set": {"revoked": True}},
        )
        result = await self._references.update_one(
            {
                "_id": claim.reference_id,
                "cleanup_state": "claimed",
                "cleanup_owner": owner,
            },
            {
                "$set": {
                    "active": False,
                    "sync_state": "inactive",
                    "cleanup_state": "deleting",
                }
            },
        )
        return result.modified_count == 1

    async def recheck_claim(
        self, reference_id: str, *, owner: str, now: datetime
    ) -> ApprovalCleanupClaim | None:
        """Reload a due, exclusively leased reference before side effects."""
        document = await self._references.find_one(
            {
                "_id": reference_id,
                "cleanup_state": "deleting",
                "cleanup_owner": owner,
                "cleanup_lease_until": {"$gt": now},
                "approval_expires_at": {"$lte": now},
                "active": False,
            }
        )
        return None if document is None else _claim(document)

    async def record_deleted_message(
        self, reference_id: str, message_id: int, *, owner: str
    ) -> bool:
        """Record deletion without duplicating message progress."""
        result = await self._references.update_one(
            {
                "_id": reference_id,
                "cleanup_state": "deleting",
                "cleanup_owner": owner,
            },
            {"$addToSet": {"cleanup_deleted_message_ids": message_id}},
        )
        return result.modified_count == 1

    async def complete_cleanup(
        self, reference_id: str, *, owner: str, outcome: str
    ) -> bool:
        """Finish cleanup and release its lease."""
        if outcome not in {"deleted", "unavailable"}:
            raise ValueError("Approval cleanup outcome is invalid.")
        result = await self._references.update_one(
            {
                "_id": reference_id,
                "cleanup_state": "deleting",
                "cleanup_owner": owner,
            },
            {
                "$set": {
                    "cleanup_state": outcome,
                    "cleanup_owner": None,
                    "cleanup_lease_until": None,
                    "cleanup_next_attempt_at": None,
                }
            },
        )
        return result.modified_count == 1

    async def defer_cleanup(
        self,
        reference_id: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        category: str,
    ) -> bool:
        """Persist one safe retry category and release the lease."""
        result = await self._references.update_one(
            {
                "_id": reference_id,
                "cleanup_state": "deleting",
                "cleanup_owner": owner,
            },
            {
                "$set": {
                    "cleanup_state": "retry",
                    "cleanup_owner": None,
                    "cleanup_lease_until": None,
                    "cleanup_next_attempt_at": next_attempt_at,
                    "cleanup_last_error_category": category,
                }
            },
        )
        return result.modified_count == 1


__all__ = (
    "MongoApprovalCleanupRepository",
    "initialize_approval_cleanup_indexes",
)
