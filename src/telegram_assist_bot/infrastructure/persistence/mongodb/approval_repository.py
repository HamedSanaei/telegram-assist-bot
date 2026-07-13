"""MongoDB persistence for callbacks, approval references, selections, and retry."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.domain import (
    ApprovalDeliveryState,
    ApprovalReference,
    ApprovalSyncState,
    CallbackAction,
    CallbackClaims,
    DestinationSelection,
    SelectionAudit,
    SelectionMode,
)

if TYPE_CHECKING:
    from datetime import datetime

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


async def initialize_approval_indexes(
    callbacks: AsyncCollection[Document],
    references: AsyncCollection[Document],
    selections: AsyncCollection[Document],
) -> None:
    """Create idempotent Milestone 3 unique, TTL, lookup, and retry indexes."""
    await callbacks.create_index(
        [("expires_at", ASCENDING)], expireAfterSeconds=0, name="ttl_callbacks_v1"
    )
    await callbacks.create_index(
        [("post_id", ASCENDING), ("revoked", ASCENDING)], name="ix_callbacks_post_v1"
    )
    await references.create_index(
        [("post_id", ASCENDING), ("active", ASCENDING)], name="ix_approval_refs_post_v1"
    )
    await references.create_index(
        [("next_retry_at", ASCENDING), ("retry_lease_until", ASCENDING)],
        name="ix_approval_retry_v1",
    )
    await selections.create_index(
        [("post_id", ASCENDING), ("destination_id", ASCENDING)],
        unique=True,
        name="uq_selection_identity_v1",
    )


def _claims(document: Document) -> CallbackClaims:
    return CallbackClaims(
        document["_id"],
        document["actor_id"],
        CallbackAction(document["action"]),
        document["post_id"],
        document.get("destination_id"),
        document["issued_at"],
        document["expires_at"],
        document.get("version", 1),
        document.get("revoked", False),
        document.get("correlation_id"),
    )


def _reference(document: Document) -> ApprovalReference:
    active = document.get("active", True)
    content_ids = tuple(document.get("content_message_ids", ()))
    state = document.get("delivery_state")
    if state is None:
        state = (
            "completed" if active else ("content_sent" if content_ids else "pending")
        )
    return ApprovalReference(
        document["_id"],
        document["actor_id"],
        document["chat_id"],
        document["post_id"],
        document["header_message_id"],
        content_ids,
        document.get("rendered_version", 0),
        active,
        ApprovalSyncState(document.get("sync_state", "current")),
        document.get("attempt_count", 0),
        document.get("next_retry_at"),
        document.get("last_error_category"),
        ApprovalDeliveryState(state),
    )


def _selection(
    document: Document | None, post_id: str, destination_id: int
) -> DestinationSelection:
    if document is None:
        return DestinationSelection(post_id, destination_id)
    history = tuple(
        SelectionAudit(
            item["actor_id"],
            SelectionMode(item["previous"]),
            SelectionMode(item["current"]),
            item["occurred_at"],
            item["correlation_id"],
        )
        for item in document.get("history", [])
    )
    return DestinationSelection(
        post_id,
        destination_id,
        SelectionMode(document.get("mode", "none")),
        document.get("version", 0),
        history,
    )


class MongoApprovalRepository:
    """Implement application-owned approval operations with MongoDB atomicity."""

    def __init__(
        self,
        callbacks: AsyncCollection[Document],
        references: AsyncCollection[Document],
        selections: AsyncCollection[Document],
    ) -> None:
        """Store concrete MongoDB collections without exposing them outward."""
        self._callbacks = callbacks
        self._references = references
        self._selections = selections

    async def insert_callback(self, claims: CallbackClaims) -> None:
        """Insert an opaque-token digest and server-only claims."""
        await self._callbacks.insert_one(
            {
                "_id": claims.token_digest,
                "actor_id": claims.actor_id,
                "action": claims.action.value,
                "post_id": claims.post_id,
                "destination_id": claims.destination_id,
                "issued_at": claims.issued_at,
                "expires_at": claims.expires_at,
                "version": claims.version,
                "revoked": claims.revoked,
                "correlation_id": claims.correlation_id,
            }
        )

    async def get_callback(self, digest: str) -> CallbackClaims | None:
        """Load callback claims by SHA-256 digest."""
        document = await self._callbacks.find_one({"_id": digest})
        return None if document is None else _claims(document)

    async def consume_callback(self, digest: str) -> bool:
        """Atomically revoke one currently valid callback token."""
        result = await self._callbacks.update_one(
            {"_id": digest, "revoked": False}, {"$set": {"revoked": True}}
        )
        return result.modified_count == 1

    async def revoke_post_callbacks(self, post_id: str) -> int:
        """Revoke all still-actionable tokens for one Post."""
        result = await self._callbacks.update_many(
            {"post_id": post_id, "revoked": False}, {"$set": {"revoked": True}}
        )
        return result.modified_count

    async def save_reference(self, reference: ApprovalReference) -> ApprovalReference:
        """Insert a stable delivery identity or return its canonical record."""
        document = {
            "_id": reference.reference_id,
            "actor_id": reference.actor_id,
            "chat_id": reference.chat_id,
            "post_id": reference.post_id,
            "header_message_id": reference.header_message_id,
            "content_message_ids": list(reference.content_message_ids),
            "rendered_version": reference.rendered_version,
            "active": reference.active,
            "sync_state": reference.sync_state.value,
            "attempt_count": reference.attempt_count,
            "next_retry_at": reference.next_retry_at,
            "last_error_category": reference.last_error_category,
            "delivery_state": reference.delivery_state.value,
        }
        try:
            await self._references.insert_one(document)
        except DuplicateKeyError:
            existing = await self._references.find_one({"_id": reference.reference_id})
            if existing is None:
                raise
            return _reference(existing)
        return reference

    async def get_reference(self, reference_id: str) -> ApprovalReference | None:
        """Load successful or partial delivery progress."""
        document = await self._references.find_one({"_id": reference_id})
        return None if document is None else _reference(document)

    async def save_delivery_progress(
        self, reference: ApprovalReference
    ) -> ApprovalReference:
        """Upsert one inactive content/control delivery phase monotonically."""
        document = await self._references.find_one_and_update(
            {"_id": reference.reference_id, "active": {"$ne": True}},
            {
                "$set": {
                    "actor_id": reference.actor_id,
                    "chat_id": reference.chat_id,
                    "post_id": reference.post_id,
                    "header_message_id": reference.header_message_id,
                    "content_message_ids": list(reference.content_message_ids),
                    "rendered_version": reference.rendered_version,
                    "active": False,
                    "sync_state": reference.sync_state.value,
                    "attempt_count": reference.attempt_count,
                    "next_retry_at": reference.next_retry_at,
                    "last_error_category": reference.last_error_category,
                    "delivery_state": reference.delivery_state.value,
                }
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            document = await self._references.find_one({"_id": reference.reference_id})
        if document is None:
            raise ValueError("Approval delivery progress does not exist.")
        return _reference(document)

    async def complete_reference(
        self, reference_id: str, control_message_id: int
    ) -> ApprovalReference:
        """Activate exactly one reference after identifiable control success."""
        document = await self._references.find_one_and_update(
            {"_id": reference_id, "active": False},
            {
                "$set": {
                    "header_message_id": control_message_id,
                    "active": True,
                    "sync_state": "current",
                    "delivery_state": "completed",
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            document = await self._references.find_one({"_id": reference_id})
        if document is None:
            raise ValueError("Approval delivery progress does not exist.")
        return _reference(document)

    async def list_active_references(
        self, post_id: str
    ) -> tuple[ApprovalReference, ...]:
        """List active references deterministically."""
        cursor = self._references.find({"post_id": post_id, "active": True}).sort(
            "_id", ASCENDING
        )
        return tuple([_reference(item) async for item in cursor])

    async def get_selection(
        self, post_id: str, destination_id: int
    ) -> DestinationSelection:
        """Read current selection with a legacy none/version-zero default."""
        return _selection(
            await self._selections.find_one(
                {"post_id": post_id, "destination_id": destination_id}
            ),
            post_id,
            destination_id,
        )

    async def compare_and_set_selection(
        self, current: DestinationSelection, updated: DestinationSelection
    ) -> bool:
        """Apply one atomic expected-version selection update."""
        audit = updated.history[-1]
        update = {
            "$set": {"mode": updated.mode.value, "version": updated.version},
            "$push": {
                "history": {
                    "actor_id": audit.actor_id,
                    "previous": audit.previous.value,
                    "current": audit.current.value,
                    "occurred_at": audit.occurred_at,
                    "correlation_id": audit.correlation_id,
                }
            },
            "$setOnInsert": {
                "post_id": updated.post_id,
                "destination_id": updated.destination_id,
            },
        }
        version_filter: Document = {"version": current.version}
        if current.version == 0:
            version_filter = {"$or": [{"version": 0}, {"version": {"$exists": False}}]}
        try:
            result = await self._selections.update_one(
                {
                    "post_id": current.post_id,
                    "destination_id": current.destination_id,
                    **version_filter,
                },
                update,
                upsert=current.version == 0,
            )
        except DuplicateKeyError:
            return False
        return bool(result.modified_count or result.upserted_id)

    async def mark_sync_success(self, reference_id: str, version: int) -> bool:
        """Persist successful rendering without allowing version rollback."""
        result = await self._references.update_one(
            {"_id": reference_id, "rendered_version": {"$lte": version}},
            {
                "$set": {
                    "rendered_version": version,
                    "sync_state": "current",
                    "attempt_count": 0,
                    "next_retry_at": None,
                    "last_error_category": None,
                },
                "$unset": {"retry_lease_until": ""},
            },
        )
        return result.modified_count == 1

    async def mark_sync_failure(
        self,
        reference_id: str,
        version: int,
        *,
        category: str,
        next_retry_at: datetime | None,
        inactive: bool,
    ) -> bool:
        """Persist only a safe category and bounded retry metadata."""
        result = await self._references.update_one(
            {"_id": reference_id, "rendered_version": {"$lte": version}},
            {
                "$set": {
                    "active": not inactive,
                    "sync_state": "inactive" if inactive else "retry",
                    "next_retry_at": next_retry_at,
                    "last_error_category": category,
                },
                "$inc": {"attempt_count": 1},
            },
        )
        return result.modified_count == 1

    async def claim_retry(
        self, reference_id: str, *, now: datetime, lease_until: datetime
    ) -> bool:
        """Claim one due retry at most once for its active lease."""
        document = await self._references.find_one_and_update(
            {
                "_id": reference_id,
                "active": True,
                "sync_state": "retry",
                "attempt_count": {"$lt": 3},
                "next_retry_at": {"$lte": now},
                "$or": [
                    {"retry_lease_until": None},
                    {"retry_lease_until": {"$lte": now}},
                ],
            },
            {"$set": {"retry_lease_until": lease_until}},
            return_document=ReturnDocument.AFTER,
        )
        return document is not None


__all__ = ("MongoApprovalRepository", "initialize_approval_indexes")
