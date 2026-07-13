"""MongoDB outbox, leases, status, and prepared approval loading."""

from __future__ import annotations

from contextlib import suppress
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ports import (
    ApprovalAdministratorDeliveryState,
    ApprovalContent,
    ApprovalDeliveryClaim,
    ApprovalMedia,
    ApprovalPost,
    ApprovalSyncClaim,
    DestinationPublicationState,
)
from telegram_assist_bot.domain.posts import TelegramEntity

if TYPE_CHECKING:
    from datetime import datetime

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


async def initialize_operational_approval_indexes(
    deliveries: AsyncCollection[Document],
) -> None:
    """Create the durable delivery claim and retry index."""
    await deliveries.create_index(
        [
            ("status", ASCENDING),
            ("claim_due_at", ASCENDING),
            ("created_at", ASCENDING),
            ("lease_until", ASCENDING),
            ("_id", ASCENDING),
        ],
        name="ix_approval_delivery_claim_v2",
    )


class MongoRuntimeHeartbeatRepository:
    """Persist and inspect safe operational-runtime liveness heartbeats."""

    def __init__(self, heartbeats: AsyncCollection[Document]) -> None:
        self._heartbeats = heartbeats

    async def beat(
        self, instance_id: str, *, started_at: datetime, now: datetime, status: str
    ) -> None:
        """Upsert one safe heartbeat without session or provider metadata."""
        await self._heartbeats.update_one(
            {"_id": instance_id},
            {
                "$set": {
                    "instance_id": instance_id,
                    "started_at": started_at,
                    "last_seen_at": now,
                    "status": status,
                }
            },
            upsert=True,
        )

    async def is_active(self, *, now: datetime, stale_after_seconds: float) -> bool:
        """Return whether any running instance has a fresh heartbeat."""
        return (
            await self._heartbeats.find_one(
                {
                    "status": "running",
                    "last_seen_at": {
                        "$gte": now - timedelta(seconds=stale_after_seconds)
                    },
                },
                projection={"_id": 1},
            )
            is not None
        )


def _entities(values: list[Document]) -> tuple[TelegramEntity, ...]:
    return tuple(
        TelegramEntity(
            int(item.get("offset_utf16", item.get("offset", 0)) or 0),
            int(item.get("length_utf16", item.get("length", 0)) or 0),
            item["entity_type"],
            item.get("custom_emoji_id"),
        )
        for item in values
    )


class MongoOperationalApprovalRepository:
    """Implement a restart-safe logical delivery outbox over ready preparations."""

    def __init__(
        self,
        preparations: AsyncCollection[Document],
        deliveries: AsyncCollection[Document],
        *,
        max_attempts: int = 3,
    ) -> None:
        """Store ready-preparation and durable-delivery collections."""
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self._preparations = preparations
        self._deliveries = deliveries
        self._max_attempts = max_attempts

    async def claim_ready(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        ready_after: datetime | None = None,
        ready_before_or_at: datetime | None = None,
    ) -> ApprovalDeliveryClaim | None:
        """Seed missing outbox identities, then claim one eligible delivery."""
        cursor = self._preparations.find(
            {"ready_at": {"$exists": True}}, projection={"_id": 1, "ready_at": 1}
        ).sort([("ready_at", ASCENDING), ("_id", ASCENDING)])
        async for item in cursor:
            with suppress(DuplicateKeyError):
                await self._deliveries.insert_one(
                    {
                        "_id": item["_id"],
                        "status": "pending",
                        "ready_at": item["ready_at"],
                        "created_at": item["ready_at"],
                        "claim_due_at": item["ready_at"],
                        "attempt_count": 0,
                        "administrator_deliveries": {},
                        "destination_statuses": {},
                        "sync_version": 0,
                        "sync_required": False,
                    }
                )
            await self._deliveries.update_one(
                {"_id": item["_id"], "claim_due_at": {"$exists": False}},
                {
                    "$set": {
                        "claim_due_at": item["ready_at"],
                        "created_at": item["ready_at"],
                        "administrator_deliveries": {},
                    }
                },
            )
        query: Document = {
            "$or": [
                {"status": "pending"},
                {"status": "retry", "next_attempt_at": {"$lte": now}},
                {"status": "claimed", "lease_until": {"$lte": now}},
            ]
        }
        if ready_after is not None and ready_before_or_at is not None:
            raise ValueError("Approval claim watermark bounds are mutually exclusive.")
        if ready_after is not None:
            query["ready_at"] = {"$gt": ready_after}
        elif ready_before_or_at is not None:
            query["ready_at"] = {"$lte": ready_before_or_at}
        document = await self._deliveries.find_one_and_update(
            query,
            {
                "$set": {
                    "status": "claimed",
                    "claim_owner": owner,
                    "lease_until": lease_until,
                    "next_attempt_at": None,
                    "claim_due_at": lease_until,
                },
                "$inc": {"attempt_count": 1},
            },
            sort=[
                ("claim_due_at", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        states = tuple(
            ApprovalAdministratorDeliveryState(
                int(identifier),
                value.get("status", "pending"),
                int(value.get("attempt_count", 0)),
                value.get("next_attempt_at"),
                value.get("delivery_phase", "pending"),
                value.get("failure_type"),
            )
            for identifier, value in document.get(
                "administrator_deliveries", {}
            ).items()
        )
        return ApprovalDeliveryClaim(
            document["_id"],
            owner,
            lease_until,
            document["ready_at"],
            int(document.get("attempt_count", 0)),
            states,
        )

    async def complete_delivery(self, post_id: str, *, owner: str) -> bool:
        """Complete one logical delivery only for its current lease owner."""
        result = await self._deliveries.update_one(
            {"_id": post_id, "status": "claimed", "claim_owner": owner},
            {
                "$set": {
                    "status": "completed",
                    "claim_owner": None,
                    "lease_until": None,
                    "claim_due_at": None,
                }
            },
        )
        return result.modified_count == 1

    async def release_delivery(
        self,
        post_id: str,
        *,
        owner: str,
        category: str,
        next_attempt_at: datetime,
        failure_type: str | None = None,
        delivery_phase: str | None = None,
        terminal: bool = False,
    ) -> bool:
        """Release one owned delivery with a safe retry category."""
        result = await self._deliveries.update_one(
            {"_id": post_id, "status": "claimed", "claim_owner": owner},
            {
                "$set": {
                    "status": "permanent_failed" if terminal else "retry",
                    "claim_owner": None,
                    "lease_until": None,
                    "next_attempt_at": next_attempt_at,
                    "claim_due_at": None if terminal else next_attempt_at,
                    "last_error_category": category,
                    "last_failure_type": failure_type,
                    "last_delivery_phase": delivery_phase,
                }
            },
        )
        return result.modified_count == 1

    async def record_administrator_delivery(
        self,
        post_id: str,
        administrator_id: int,
        *,
        owner: str,
        status: str,
        attempt_count: int,
        delivery_phase: str,
        next_attempt_at: datetime | None = None,
        failure_category: str | None = None,
        failure_type: str | None = None,
    ) -> bool:
        """Persist one administrator result without resetting other progress."""
        key = f"administrator_deliveries.{administrator_id}"
        result = await self._deliveries.update_one(
            {"_id": post_id, "status": "claimed", "claim_owner": owner},
            {
                "$set": {
                    key: {
                        "status": status,
                        "attempt_count": attempt_count,
                        "next_attempt_at": next_attempt_at,
                        "delivery_phase": delivery_phase,
                        "failure_category": failure_category,
                        "failure_type": failure_type,
                    }
                }
            },
        )
        return result.modified_count == 1

    async def retry_delivery(self, post_id: str, *, now: datetime) -> bool:
        """Idempotently requeue only failed administrator phases for one Post."""
        document = await self._deliveries.find_one({"_id": post_id})
        if document is None or document.get("status") in {
            "pending",
            "retry",
            "claimed",
            "completed",
        }:
            return False
        states = document.get("administrator_deliveries", {})
        reset = {
            identifier: {
                **value,
                "status": "retry"
                if value.get("status") == "permanent_failed"
                else value.get("status"),
                "attempt_count": 0
                if value.get("status") == "permanent_failed"
                else value.get("attempt_count", 0),
                "next_attempt_at": now
                if value.get("status") == "permanent_failed"
                else value.get("next_attempt_at"),
            }
            for identifier, value in states.items()
        }
        result = await self._deliveries.update_one(
            {"_id": post_id, "status": "permanent_failed"},
            {
                "$set": {
                    "status": "retry",
                    "next_attempt_at": now,
                    "claim_due_at": now,
                    "administrator_deliveries": reset,
                }
            },
        )
        return result.modified_count == 1

    async def is_actionable(self, post_id: str) -> bool:
        """Return whether the Post has durable ready preparation state."""
        return (
            await self._preparations.find_one(
                {"_id": post_id, "ready_at": {"$exists": True}}, projection={"_id": 1}
            )
            is not None
        )

    async def record_destination_status(
        self,
        post_id: str,
        destination_id: int,
        *,
        status: str,
        version: int,
        at: datetime,
        action: str | None = None,
        due_at: datetime | None = None,
    ) -> None:
        """Persist one monotonic safe destination status and request UI sync."""
        key = f"destination_statuses.{destination_id}"
        await self._deliveries.update_one(
            {"_id": post_id, f"{key}.version": {"$not": {"$gt": version}}},
            {
                "$set": {
                    key: {
                        "status": status,
                        "version": version,
                        "updated_at": at,
                        "action": action,
                        "due_at": due_at,
                    },
                    "sync_required": True,
                },
                "$inc": {"sync_version": 1},
            },
        )

    async def destination_statuses(self, post_id: str) -> dict[int, str]:
        """Return detached safe status values for one approval."""
        document = await self._deliveries.find_one(
            {"_id": post_id}, projection={"destination_statuses": 1}
        )
        if document is None:
            return {}
        return {
            int(key): value["status"]
            for key, value in document.get("destination_statuses", {}).items()
        }

    async def destination_states(
        self, post_id: str
    ) -> dict[int, DestinationPublicationState]:
        """Return safe durable status and timing metadata for control cards."""
        document = await self._deliveries.find_one(
            {"_id": post_id}, projection={"destination_statuses": 1}
        )
        if document is None:
            return {}
        return {
            int(key): DestinationPublicationState(
                value["status"],
                value.get("action"),
                value["updated_at"],
                value.get("due_at"),
            )
            for key, value in document.get("destination_statuses", {}).items()
        }

    async def claim_sync(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> ApprovalSyncClaim | None:
        """Lease one pending UI synchronization request."""
        document = await self._deliveries.find_one_and_update(
            {
                "sync_required": True,
                "$or": [
                    {"sync_lease_until": None},
                    {"sync_lease_until": {"$exists": False}},
                    {"sync_lease_until": {"$lte": now}},
                ],
            },
            {"$set": {"sync_owner": owner, "sync_lease_until": lease_until}},
            sort=[("ready_at", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            return None
        return ApprovalSyncClaim(
            document["_id"], document.get("sync_version", 0), owner
        )

    async def complete_sync(self, post_id: str, *, owner: str, version: int) -> bool:
        """Complete a sync only when no newer status version superseded it."""
        result = await self._deliveries.update_one(
            {"_id": post_id, "sync_owner": owner, "sync_version": version},
            {
                "$set": {
                    "sync_required": False,
                    "sync_owner": None,
                    "sync_lease_until": None,
                }
            },
        )
        return result.modified_count == 1


class MongoApprovalPostLoader:
    """Join ready content, source metadata, and ordered private media paths."""

    def __init__(
        self,
        posts: AsyncCollection[Document],
        preparations: AsyncCollection[Document],
        media: AsyncCollection[Document],
        groups: AsyncCollection[Document],
        *,
        destination_names: tuple[str, ...],
    ) -> None:
        """Store source and preparation collections plus destination order."""
        self._posts = posts
        self._preparations = preparations
        self._media = media
        self._groups = groups
        self._destination_names = destination_names

    async def load(self, post_id: str) -> ApprovalPost:
        """Load exact prepared text, entities, and ordered media for approval."""
        preparation = await self._preparations.find_one(
            {"_id": post_id, "ready_at": {"$exists": True}}
        )
        post = await self._posts.find_one({"_id": post_id})
        if preparation is None or post is None:
            raise ValueError("Ready approval content does not exist.")
        artifacts = preparation.get("artifacts", {})
        artifact = next(
            (artifacts[name] for name in self._destination_names if name in artifacts),
            None,
        )
        original = post["original_content"]
        text = original.get("text")
        caption = original.get("caption")
        text_entities = _entities(original.get("text_entities", []))
        caption_entities = _entities(original.get("caption_entities", []))
        if artifact is not None:
            prepared_text = artifact.get("text")
            if caption is not None:
                caption = prepared_text
                caption_entities = _entities(artifact.get("entities", []))
            else:
                text = prepared_text
                text_entities = _entities(artifact.get("entities", []))
        group = await self._groups.find_one(
            {
                "source_channel_id": post["source_channel_id"],
                "members.source_message_id": post["source_message_id"],
                "finalized_at": {"$ne": None},
            }
        )
        if group is None:
            cursor = self._media.find(
                {
                    "source_channel_id": post["source_channel_id"],
                    "source_message_id": post["source_message_id"],
                    "cleaned_at": None,
                }
            ).sort("item_index", ASCENDING)
            media_documents = [item async for item in cursor]
            approval_media = tuple(
                ApprovalMedia(
                    str(item.get("media_type", "document")),
                    str(item["storage_path"]),
                    item.get("mime_type"),
                    item.get("original_filename"),
                )
                for item in media_documents
            )
            paths = tuple(item.storage_path for item in approval_media)
            content_type = (
                str(media_documents[0].get("media_type", "document")).lower()
                if media_documents
                else "text"
            )
        else:
            approval_media = tuple(
                ApprovalMedia(
                    str(item["media"].get("media_type", "document")),
                    str(item["media"]["storage_path"]),
                    item["media"].get("mime_type"),
                    item["media"].get("original_filename"),
                )
                for item in group["members"]
            )
            paths = tuple(item.storage_path for item in approval_media)
            content_type = "album"
        category = preparation.get("category_result", {}).get("category_id")
        duplicate_value = preparation.get("duplicate_result", {}).get("is_duplicate")
        duplicate = (
            None if duplicate_value is None else ("بله" if duplicate_value else "خیر")
        )
        return ApprovalPost(
            post_id,
            post["source_channel_display_name"],
            post.get("source_channel_username"),
            post["source_channel_id"],
            ApprovalContent(
                text,
                caption,
                text_entities,
                caption_entities,
                paths,
                approval_media,
            ),
            category=category,
            duplicate=duplicate,
            source_message_id=post.get("source_message_id"),
            source_published_at=post.get("source_published_at"),
            content_type=content_type,
            media_count=len(paths),
        )


__all__ = (
    "MongoApprovalPostLoader",
    "MongoOperationalApprovalRepository",
    "initialize_operational_approval_indexes",
)
