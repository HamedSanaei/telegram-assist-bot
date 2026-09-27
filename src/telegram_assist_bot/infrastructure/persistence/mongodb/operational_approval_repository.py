"""MongoDB outbox, leases, status, and prepared approval loading.

The delivery outbox is the only collection the polling claim path touches. One
identity per durably ready preparation is created at the readiness transition and
missing legacy identities are backfilled by a bounded, watermark-anchored
reconciliation instead of a historical scan on every poll.
"""

from __future__ import annotations

from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, OperationFailure

from telegram_assist_bot.application.ports import (
    ApprovalAdministratorDeliveryState,
    ApprovalContent,
    ApprovalDeliveryClaim,
    ApprovalMedia,
    ApprovalOutboxReconciliation,
    ApprovalPost,
    ApprovalSyncClaim,
    DestinationPublicationState,
)
from telegram_assist_bot.domain.posts import TelegramEntity

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.domain.categories import Category

type Document = dict[str, Any]

APPROVAL_CLAIM_SORT: list[tuple[str, int]] = [
    ("claim_due_at", ASCENDING),
    ("created_at", ASCENDING),
    ("_id", ASCENDING),
]
"""Stable claim order shared by the repository and its query-plan proof."""


def approval_claim_filter(
    *,
    now: datetime,
    ready_after: datetime | None = None,
    ready_before_or_at: datetime | None = None,
) -> Document:
    """Return the exact indexed claim predicate used by the polling hot path.

    The predicate only references `approval_deliveries` fields, so no poll can
    ever enumerate readiness documents or historical preparations.
    """
    if ready_after is not None and ready_before_or_at is not None:
        raise ValueError("Approval claim watermark bounds are mutually exclusive.")
    query: Document = {
        "$or": [
            {"status": "pending"},
            {"status": "retry", "next_attempt_at": {"$lte": now}},
            {"status": "claimed", "lease_until": {"$lte": now}},
        ]
    }
    if ready_after is not None:
        query["ready_at"] = {"$gt": ready_after}
    elif ready_before_or_at is not None:
        query["ready_at"] = {"$lte": ready_before_or_at}
    return query


INITIAL_RECONCILIATION_WATERMARK: tuple[datetime, str] = (
    datetime.min.replace(tzinfo=UTC),
    "",
)
"""First reconciliation position: before every possible readiness instant.

A concrete lower bound keeps the first batch on the readiness index instead of a
collection scan, because a datetime range excludes documents without `ready_at`
through MongoDB type bracketing.
"""


def outbox_reconciliation_filter(
    watermark: tuple[datetime, str] | None,
    *,
    ready_before_or_at: datetime | None = None,
) -> Document:
    """Return the bounded readiness scan predicate of one reconciliation batch.

    `ready_before_or_at` is the guard horizon of the trailing re-verification
    window. When it is provided, the batch can never advance past a readiness
    marker newer than that horizon, so the newest markers are re-verified by the
    next passes and an identity whose inline creation failed is still healed
    without ever rescanning processed history. Callers must rewind a durable
    watermark that already reached the horizon, otherwise that window would be
    excluded by the upper bound instead of being verified.
    """
    ready_at, identifier = watermark or INITIAL_RECONCILIATION_WATERMARK
    forward: Document = {"ready_at": {"$gt": ready_at}}
    if ready_before_or_at is not None:
        forward["ready_at"]["$lte"] = ready_before_or_at
    return {
        "$or": [
            forward,
            {"ready_at": ready_at, "_id": {"$gt": identifier}},
        ]
    }


_RECONCILIATION_STATE_ID = "content_preparation_outbox"
"""Document identifier of the durable outbox reconciliation watermark."""

_MAX_RECONCILIATION_BATCH = 1000
"""Upper bound of one reconciliation batch so no poll can become unbounded."""


async def initialize_operational_approval_indexes(
    deliveries: AsyncCollection[Document],
) -> None:
    """Create the durable claim, retry, lease, and UI-sync delivery indexes.

    The claim index bounds every branch of the polling query by leading equality
    on `status`, walks `claim_due_at`/`created_at`/`_id` in the exact claim sort
    order, and keeps the retry and lease predicates inside the index so the query
    planner never has to fetch non-matching delivery documents. The small partial
    index serves the `sync_required` polling query that previously had no index.
    """
    await deliveries.create_index(
        [
            ("status", ASCENDING),
            ("claim_due_at", ASCENDING),
            ("created_at", ASCENDING),
            ("_id", ASCENDING),
            ("lease_until", ASCENDING),
            ("next_attempt_at", ASCENDING),
        ],
        name="ix_approval_delivery_claim_v3",
    )
    await deliveries.create_index(
        [("ready_at", ASCENDING), ("_id", ASCENDING)],
        name="ix_approval_delivery_sync_v1",
        partialFilterExpression={"sync_required": True},
    )
    with suppress(OperationFailure):
        await deliveries.drop_index("ix_approval_delivery_claim_v2")


def _outbox_document(post_id: str, *, ready_at: datetime) -> Document:
    """Build the initial durable delivery identity for one ready preparation."""
    return {
        "status": "pending",
        "ready_at": ready_at,
        "created_at": ready_at,
        "claim_due_at": ready_at,
        "attempt_count": 0,
        "administrator_deliveries": {},
        "destination_statuses": {},
        "sync_version": 0,
        "sync_required": False,
    }


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
            item.get("url"),
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
        outbox_state: AsyncCollection[Document] | None = None,
    ) -> None:
        """Store readiness, durable-delivery, and reconciliation-state handles."""
        if not 1 <= max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        self._preparations = preparations
        self._deliveries = deliveries
        self._max_attempts = max_attempts
        self._outbox_state = outbox_state

    async def ensure_delivery(self, post_id: str, *, ready_at: datetime) -> bool:
        """Create one missing durable delivery identity without touching history.

        The upsert is idempotent, safe under duplicate and concurrent execution,
        and never overwrites an existing identity, its progress, or its status. A
        pending identity written before ordering fields existed is repaired in
        place so historical ordering semantics stay correct.
        """
        result = await self._deliveries.update_one(
            {"_id": post_id},
            {"$setOnInsert": _outbox_document(post_id, ready_at=ready_at)},
            upsert=True,
        )
        if result.upserted_id is not None:
            return True
        with suppress(DuplicateKeyError):
            await self._deliveries.update_one(
                {
                    "_id": post_id,
                    "status": "pending",
                    "claim_due_at": {"$exists": False},
                },
                {"$set": {"claim_due_at": ready_at, "created_at": ready_at}},
            )
        return False

    async def reconcile_missing_deliveries(
        self,
        *,
        limit: int,
        at: datetime,
        guard_seconds: float = 0.0,
    ) -> ApprovalOutboxReconciliation:
        """Backfill missing identities for legacy ready preparations in one batch.

        The scan is bounded by `limit`, ordered by the readiness key, anchored on a
        durable watermark, and therefore never repeats processed history. A batch
        smaller than `limit` means the catch-up reached the end of the collected
        readiness markers.

        A positive `guard_seconds` holds the watermark back by that many seconds,
        which turns the newest readiness markers into a bounded trailing window
        that every pass re-verifies. That window is the safety net for an identity
        whose inline creation failed after its preparation was already marked
        ready; it stays bounded by ingestion volume and never scans history.
        """
        if self._outbox_state is None:
            raise ValueError("Outbox reconciliation requires its state collection.")
        if type(limit) is not int or not 1 <= limit <= _MAX_RECONCILIATION_BATCH:
            raise ValueError(
                "Outbox reconciliation limit must be between 1 and "
                f"{_MAX_RECONCILIATION_BATCH}."
            )
        if type(guard_seconds) is bool or float(guard_seconds) < 0:
            raise ValueError("Outbox reconciliation guard must not be negative.")
        horizon = (
            None
            if float(guard_seconds) == 0
            else at - timedelta(seconds=float(guard_seconds))
        )
        watermark = await self._read_reconciliation_watermark()
        if horizon is not None and watermark is not None and watermark[0] >= horizon:
            # A raised guard puts the durable watermark at or beyond the window, so
            # the deferred markers would never be scanned again. Rewind the durable
            # watermark to the horizon once; later passes then verify the window
            # forward from that bound instead of skipping it forever.
            watermark = (horizon, "")
            await self._write_reconciliation_watermark(watermark, at=at)
        query = outbox_reconciliation_filter(watermark, ready_before_or_at=horizon)
        cursor = (
            self._preparations.find(query, projection={"_id": 1, "ready_at": 1})
            .sort([("ready_at", ASCENDING), ("_id", ASCENDING)])
            .limit(limit)
        )
        candidates = [item async for item in cursor]
        known_ids: set[str] = set()
        if candidates:
            known = self._deliveries.find(
                {"_id": {"$in": [item["_id"] for item in candidates]}},
                projection={"_id": 1},
            )
            known_ids = {str(item["_id"]) async for item in known}
        created_count = 0
        existing_count = 0
        last_key: tuple[datetime, str] | None = None
        for item in candidates:
            post_id = str(item["_id"])
            last_key = (item["ready_at"], post_id)
            if post_id in known_ids:
                existing_count += 1
                continue
            if await self.ensure_delivery(post_id, ready_at=item["ready_at"]):
                created_count += 1
            else:
                existing_count += 1
        if last_key is not None:
            await self._write_reconciliation_watermark(last_key, at=at)
        return ApprovalOutboxReconciliation(
            scanned_count=len(candidates),
            created_count=created_count,
            existing_count=existing_count,
            completed=len(candidates) < limit,
            watermark=None if last_key is None else last_key[0],
        )

    async def _read_reconciliation_watermark(self) -> tuple[datetime, str] | None:
        """Load the durable reconciliation position when it already exists."""
        if self._outbox_state is None:
            return None
        document = await self._outbox_state.find_one(
            {"_id": _RECONCILIATION_STATE_ID},
            projection={"watermark_ready_at": 1, "watermark_id": 1},
        )
        if document is None:
            return None
        ready_at = document.get("watermark_ready_at")
        identifier = document.get("watermark_id")
        if type(ready_at) is not datetime or type(identifier) is not str:
            return None
        return ready_at, identifier

    async def _write_reconciliation_watermark(
        self, key: tuple[datetime, str], *, at: datetime
    ) -> None:
        """Persist the exact reconciliation position of the last scanned batch."""
        if self._outbox_state is None:
            return
        ready_at, identifier = key
        await self._outbox_state.update_one(
            {"_id": _RECONCILIATION_STATE_ID},
            {
                "$set": {
                    "watermark_ready_at": ready_at,
                    "watermark_id": identifier,
                    "updated_at": at,
                }
            },
            upsert=True,
        )

    async def claim_ready(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        ready_after: datetime | None = None,
        ready_before_or_at: datetime | None = None,
    ) -> ApprovalDeliveryClaim | None:
        """Claim one pending, retry-due, or lease-expired delivery identity.

        This is the polling hot path and stays a single indexed claim over
        `approval_deliveries`. It never enumerates or writes historical
        `content_preparations` documents.
        """
        query = approval_claim_filter(
            now=now,
            ready_after=ready_after,
            ready_before_or_at=ready_before_or_at,
        )
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
            sort=APPROVAL_CLAIM_SORT,
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
                value.get("failure_reason"),
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
        failure_reason: str | None = None,
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
                        "failure_reason": failure_reason,
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
        if (
            await self._deliveries.find_one(
                {"_id": post_id, "approval_expired": True}, projection={"_id": 1}
            )
            is not None
        ):
            return False
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
        categories: tuple[Category, ...] = (),
    ) -> None:
        """Store source and preparation collections plus destination order."""
        self._posts = posts
        self._preparations = preparations
        self._media = media
        self._groups = groups
        self._destination_names = destination_names
        self._categories = categories

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
        category_result = preparation.get("category_result")
        if category_result is not None:
            category_id = category_result.get("category_id")
            method_str = category_result.get("method")
            confidence = category_result.get("confidence")
            provider_name = category_result.get("provider_name")
            model_name = category_result.get("model_name")

            category_display = category_id
            for cat in self._categories:
                if cat.category_id == category_id:
                    category_display = cat.display_name
                    break

            state = post.get("categorization_processing", {}).get(
                "state", "NotRequested"
            )

            parts = [category_display]
            if method_str == "Manual":
                parts.append("(دستی)")
            elif method_str == "Keyword":
                if state == "KeywordFallback":
                    parts.append("(کلمه کلیدی - پشتیبان هوش مصنوعی)")
                else:
                    parts.append("(کلمه کلیدی)")
            elif method_str == "SourceDefault":
                if state == "SourceDefaultFallback":
                    parts.append("(پیش‌فرض منبع - پشتیبان هوش مصنوعی)")
                else:
                    parts.append("(پیش‌فرض منبع)")
            elif method_str == "AI":
                ai_details = []
                if confidence is not None:
                    ai_details.append(f"{int(confidence * 100)}%")
                if provider_name and model_name:
                    ai_details.append(f"{provider_name}/{model_name}")
                elif provider_name:
                    ai_details.append(provider_name)

                if ai_details:
                    parts.append(f"(هوش مصنوعی: {' - '.join(ai_details)})")
                else:
                    parts.append("(هوش مصنوعی)")
            category = " ".join(parts)
        else:
            category = None
        duplicate_value = preparation.get("duplicate_result", {}).get("is_duplicate")
        duplicate = (
            None if duplicate_value is None else ("بله" if duplicate_value else "خیر")
        )
        scoring = post.get("scoring_processing", {})
        scoring_state = scoring.get("state", "NotRequested")
        scoring_result = scoring.get("result")
        if scoring_state == "ScoringCompleted" and isinstance(scoring_result, dict):
            raw_score = scoring_result.get("score")
            score = str(raw_score) if type(raw_score) is int else "در دسترس نیست"
        elif scoring_state in {
            "ScoringScheduled",
            "ScoringPending",
            "ScoringRetryPending",
        }:
            score = "در انتظار بررسی"
        elif scoring_state in {"ScoringUnavailable", "ScoringStaleOrExpired"}:
            score = "در دسترس نیست"
        else:
            score = None
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
            score=score,
            source_message_id=post.get("source_message_id"),
            source_published_at=post.get("source_published_at"),
            content_type=content_type,
            media_count=len(paths),
        )


__all__ = (
    "APPROVAL_CLAIM_SORT",
    "INITIAL_RECONCILIATION_WATERMARK",
    "MongoApprovalPostLoader",
    "MongoOperationalApprovalRepository",
    "approval_claim_filter",
    "initialize_operational_approval_indexes",
    "outbox_reconciliation_filter",
)
