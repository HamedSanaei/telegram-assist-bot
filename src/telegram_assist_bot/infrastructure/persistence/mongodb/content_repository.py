"""MongoDB adapter for Milestone 2 preparation metadata and atomic results."""

from __future__ import annotations

import contextlib
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import wraps
from typing import TYPE_CHECKING, Any, cast

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import ConnectionFailure, DuplicateKeyError, ExecutionTimeout

from telegram_assist_bot.application.ports import (
    AlbumFinalizationStatus,
    DestinationArtifact,
    InvalidMediaGroupRecordError,
    MediaGroup,
    MediaGroupMember,
)
from telegram_assist_bot.application.ports.media import (
    TERMINAL_MEDIA_FAILURE_CATEGORIES,
    MediaDownloadSpec,
    MediaFailure,
    MediaRateLimitError,
    MediaTransientError,
    PreparationState,
)
from telegram_assist_bot.application.ports.telegram_source_gateway import (
    TelegramMediaReference,
    TelegramTextMessage,
)
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
)
from telegram_assist_bot.domain.duplicates import DuplicateCheckResult
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.domain.posts import PostId, TelegramEntity, TelegramUrlButton

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


def _mongo_operation[**P, T](
    operation: Callable[P, Awaitable[T]],
) -> Callable[P, Coroutine[Any, Any, T]]:
    """Translate only known infrastructure outages into recoverable port errors."""

    @wraps(operation)
    async def guarded(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            return await operation(*args, **kwargs)
        except (ConnectionFailure, ExecutionTimeout, TimeoutError):
            raise MediaTransientError(
                "Content persistence is temporarily unavailable."
            ) from None

    return guarded


@dataclass(frozen=True, slots=True)
class MongoMediaReferenceCollections:
    """Hold optional MongoDB collections used only for cleanup reference checks."""

    posts: AsyncCollection[Document] | None = None
    publications: AsyncCollection[Document] | None = None
    schedules: AsyncCollection[Document] | None = None
    native_schedules: AsyncCollection[Document] | None = None
    approval_deliveries: AsyncCollection[Document] | None = None
    advertisement_sources: AsyncCollection[Document] | None = None
    advertisement_slots: AsyncCollection[Document] | None = None


@_mongo_operation
async def initialize_content_preparation_indexes(
    media: AsyncCollection[Document],
    groups: AsyncCollection[Document],
    preparations: AsyncCollection[Document],
) -> None:
    """Create restart-safe indexes for Milestone 2 collections."""
    await media.create_index(
        [("storage_path", ASCENDING)], name="ix_media_storage_path_v1"
    )
    await media.create_index(
        [("expires_at", ASCENDING), ("cleaned_at", ASCENDING)],
        name="ix_media_cleanup_v1",
    )
    await media.create_index(
        [("media_expires_at", ASCENDING), ("cleaned_at", ASCENDING)],
        name="ix_media_retention_cleanup_v2",
    )
    await media.create_index(
        [
            ("cleaned_at", ASCENDING),
            ("media_expires_at", ASCENDING),
            ("cleanup_next_check_at", ASCENDING),
        ],
        name="ix_media_cleanup_deferral_v3",
    )
    await media.create_index(
        [
            ("cleaned_at", ASCENDING),
            ("cleanup_next_check_at", ASCENDING),
            ("_id", ASCENDING),
        ],
        name="ix_media_cleanup_fairness_v4",
    )
    await media.create_index(
        [("post_id", ASCENDING), ("storage_path", ASCENDING)],
        name="ix_media_post_path_v1",
    )
    await groups.create_index(
        [("source_channel_id", ASCENDING), ("telegram_group_id", ASCENDING)],
        unique=True,
        name="uq_media_group_identity_v1",
    )
    await groups.create_index(
        [("finalized_at", ASCENDING), ("finalize_after", ASCENDING)],
        name="ix_media_group_finalization_v1",
    )
    await groups.create_index(
        [
            ("finalization_status", ASCENDING),
            ("next_attempt_at", ASCENDING),
            ("claim_expires_at", ASCENDING),
            ("finalize_after", ASCENDING),
        ],
        name="ix_media_group_claim_v1",
    )
    await preparations.create_index(
        [
            ("duplicate_result.content_hash", ASCENDING),
            ("duplicate_result.checked_at", ASCENDING),
        ],
        name="ix_exact_duplicate_window_v1",
    )
    await preparations.create_index(
        [("ready_at", ASCENDING), ("_id", ASCENDING)],
        name="ix_content_preparation_readiness_v1",
    )
    await preparations.create_index(
        [
            ("media_operation.post_id", ASCENDING),
            ("media_operation.category", ASCENDING),
        ],
        name="ix_media_operation_recovery_v1",
    )
    await preparations.create_index(
        [("recovery_requested", ASCENDING), ("recovery_not_before", ASCENDING)],
        name="ix_ingestion_recovery_v1",
    )
    await preparations.create_index(
        [("history_hold", ASCENDING), ("history_source_channel_id", ASCENDING)],
        name="ix_history_album_hold_v1",
    )
    await preparations.create_index(
        [("ingestion_snapshot_expires_at", ASCENDING)],
        expireAfterSeconds=0,
        name="ttl_ingestion_message_snapshot_v1",
    )


def expired_candidate_filter(now: datetime) -> Document:
    """Return the shared legacy-safe expiration predicate for cleanup."""
    return {
        "$or": [
            {"media_expires_at": {"$lte": now}},
            {
                "media_expires_at": {"$exists": False},
                "expires_at": {"$lte": now},
            },
        ]
    }


def never_attempted_candidate_filter(now: datetime) -> Document:
    """Select expired candidates that never received a cleanup attempt.

    `cleanup_next_check_at: None` matches an explicit null and a missing field, so
    both legacy shapes form one fast class. The equality on
    `cleanup_next_check_at` keeps the scan on the existing deferral and retention
    indexes, which bound it to expired documents instead of walking the much
    larger fresh-media population. Records written before `cleanup_next_check_at`
    existed therefore need no backfill to be selected first.
    """
    return {
        "cleaned_at": None,
        "cleanup_next_check_at": None,
        "$and": [expired_candidate_filter(now)],
    }


def due_retry_candidate_filter(now: datetime) -> Document:
    """Select expired deferred candidates whose retry instant has arrived.

    A comparison against a datetime is type-bracketed in MongoDB, so `$lte`
    matches only real dates and never a null or missing `cleanup_next_check_at`;
    the two candidate classes are therefore disjoint. `ix_media_cleanup_fairness_v4`
    serves this filter and its `cleanup_next_check_at`/`_id` ordering directly.
    """
    return {
        "cleaned_at": None,
        "cleanup_next_check_at": {"$lte": now},
        "$and": [expired_candidate_filter(now)],
    }


def _media_document(media: StoredMedia) -> Document:
    return {
        "_id": media.identity.key,
        "source_channel_id": media.identity.source_channel_id,
        "source_message_id": media.identity.source_message_id,
        "item_index": media.identity.item_index,
        "media_type": media.media_type.value,
        "content_hash": media.content_hash,
        "size_bytes": media.size_bytes,
        "mime_type": media.mime_type,
        "original_filename": media.original_filename,
        "storage_path": media.storage_path,
        "post_id": media.post_id,
        "media_expires_at": media.expires_at,
        # Retain the pre-T078 field for old readers during the additive transition.
        "expires_at": media.expires_at,
        "cleaned_at": None,
    }


def _media_from(document: Document) -> StoredMedia:
    expiration = document.get("media_expires_at")
    if expiration is None:
        expiration = document["expires_at"]
    return StoredMedia(
        MediaIdentity(
            document["source_channel_id"],
            document["source_message_id"],
            document["item_index"],
        ),
        MediaType(document["media_type"]),
        document["content_hash"],
        document["size_bytes"],
        document.get("mime_type"),
        document.get("original_filename"),
        document["storage_path"],
        cast("datetime", expiration),
        document.get("post_id"),
    )


def _entity_document(entity: TelegramEntity) -> Document:
    return {
        "offset": entity.offset_utf16,
        "length": entity.length_utf16,
        "entity_type": entity.entity_type,
        "custom_emoji_id": entity.custom_emoji_id,
        "url": entity.url,
    }


def _entity_from(document: Document) -> TelegramEntity:
    return TelegramEntity(
        document["offset"],
        document["length"],
        document["entity_type"],
        document.get("custom_emoji_id"),
        document.get("url"),
    )


def _valid_channel_id(value: object) -> int | None:
    return value if type(value) is int and value != 0 else None


def _valid_message_id(value: object) -> int | None:
    return value if type(value) is int and value > 0 else None


def _group_from_document(document: Document) -> MediaGroup:
    """Load current and legacy group documents with safe identity recovery."""
    group_key = str(document.get("_id", ""))
    attempt_count_value = document.get("attempt_count", 0)
    attempt_count = (
        attempt_count_value
        if type(attempt_count_value) is int and attempt_count_value >= 0
        else 0
    )
    raw_members = document.get("members", [])
    media_member_count = len(raw_members) if type(raw_members) is list else 0
    try:
        if not group_key or type(raw_members) is not list:
            raise ValueError
        media_channels = {
            channel
            for item in raw_members
            if type(item) is dict and type(item.get("media")) is dict
            if (channel := _valid_channel_id(item["media"].get("source_channel_id")))
            is not None
        }
        source_channel_id = _valid_channel_id(document.get("source_channel_id"))
        if source_channel_id is None and len(media_channels) == 1:
            source_channel_id = next(iter(media_channels))
        if source_channel_id is None:
            prefix, separator, _suffix = group_key.partition(":")
            parsed = int(prefix) if separator else 0
            source_channel_id = _valid_channel_id(parsed)
        if source_channel_id is None or any(
            channel != source_channel_id for channel in media_channels
        ):
            raise ValueError

        telegram_group_id_value = document.get("telegram_group_id")
        telegram_group_id = (
            telegram_group_id_value
            if type(telegram_group_id_value) is str and telegram_group_id_value
            else group_key.partition(":")[2]
        )
        if not telegram_group_id:
            raise ValueError

        members: list[MediaGroupMember] = []
        for item in raw_members:
            if type(item) is not dict or type(item.get("media")) is not dict:
                raise ValueError
            media_document = dict(item["media"])
            member_message_id = _valid_message_id(item.get("source_message_id"))
            media_message_id = _valid_message_id(
                media_document.get("source_message_id")
            )
            if member_message_id is None:
                member_message_id = media_message_id
            if member_message_id is None or (
                media_message_id is not None and media_message_id != member_message_id
            ):
                raise ValueError
            media_document["source_channel_id"] = source_channel_id
            media_document["source_message_id"] = member_message_id
            media = _media_from(media_document)
            members.append(
                MediaGroupMember(
                    member_message_id,
                    item["source_date"],
                    media,
                    item.get("caption"),
                    tuple(
                        _entity_from(entity)
                        for entity in item.get("caption_entities", [])
                    ),
                    item.get("observed_at"),
                    item.get("telegram_group_id"),
                )
            )
        members.sort(key=lambda value: (value.source_date, value.source_message_id))
        observed_values = document.get("observed_message_ids", [])
        observed_ids = {
            value for value in observed_values if _valid_message_id(value) is not None
        }
        observed_ids.update(member.source_message_id for member in members)
        status_value = document.get(
            "finalization_status", AlbumFinalizationStatus.PENDING.value
        )
        status = AlbumFinalizationStatus(status_value)
        canonical_source_message_id = _valid_message_id(
            document.get("canonical_source_message_id")
        )
        return MediaGroup(
            group_key,
            source_channel_id,
            telegram_group_id,
            tuple(members),
            document["first_member_at"],
            document["last_member_at"],
            document["finalize_after"],
            document["maximum_wait_until"],
            document.get("finalized_at"),
            tuple(sorted(observed_ids)),
            status,
            attempt_count,
            document.get("next_attempt_at"),
            document.get("claim_owner"),
            document.get("claim_expires_at"),
            document.get("failure_category"),
            canonical_source_message_id,
        )
    except (KeyError, TypeError, ValueError):
        raise InvalidMediaGroupRecordError(
            group_key or "unknown-media-group",
            attempt_count=attempt_count,
            media_member_count=media_member_count,
        ) from None


class MongoContentPreparationRepository:
    """Keep driver types and atomic preparation queries inside Infrastructure."""

    def __init__(
        self,
        media: AsyncCollection[Document],
        groups: AsyncCollection[Document],
        preparations: AsyncCollection[Document],
        *,
        references: MongoMediaReferenceCollections | None = None,
    ) -> None:
        """Initialize concrete MongoDB collections."""
        self._media, self._groups, self._preparations = media, groups, preparations
        self._references = references or MongoMediaReferenceCollections()

    @_mongo_operation
    async def set_history_hold(
        self,
        source_channel_id: int,
        *,
        held: bool,
        snapshot_end: datetime | None = None,
    ) -> None:
        """Fence source holds by traversal snapshot so stale crawls cannot reopen."""
        query: Document = {"_id": f"history-ingestion:{source_channel_id}"}
        if snapshot_end is not None:
            if held:
                query["$or"] = [
                    {"history_snapshot_end": {"$exists": False}},
                    {"history_snapshot_end": {"$lt": snapshot_end}},
                ]
            else:
                query["history_snapshot_end"] = snapshot_end
        fields: Document = {
            "history_hold": held,
            "history_source_channel_id": source_channel_id,
        }
        if snapshot_end is not None:
            fields["history_snapshot_end"] = snapshot_end
        with contextlib.suppress(DuplicateKeyError):
            await self._preparations.update_one(
                query,
                {"$set": fields},
                upsert=held,
            )

    @_mongo_operation
    async def save_ingestion_message(
        self, post_id: PostId, message: TelegramTextMessage, *, expires_at: datetime
    ) -> None:
        """Keep source payload only until the exact canonical Post expiry."""
        await self._preparations.update_one(
            {"_id": f"ingestion-message:{post_id.value}"},
            {
                "$setOnInsert": {
                    "ingestion_message": asdict(message),
                    "ingestion_snapshot_expires_at": expires_at,
                }
            },
            upsert=True,
        )
        await self._preparations.update_one(
            {"_id": post_id.value},
            {"$set": {"ingestion_snapshot": True, "recovery_requested": True}},
        )

    @_mongo_operation
    async def list_pending_ingestion(
        self, *, now: datetime, limit: int
    ) -> tuple[tuple[PostId, TelegramTextMessage], ...]:
        """Load a bounded due queue, excluding terminal rejection and live leases."""
        cursor = (
            self._preparations.find(
                {
                    "recovery_requested": True,
                    "media_rejected": {"$ne": True},
                    "ingestion_snapshot": True,
                    "$and": [
                        {
                            "$or": [
                                {"recovery_not_before": None},
                                {"recovery_not_before": {"$lte": now}},
                            ]
                        },
                        {
                            "$or": [
                                {"ingestion_owner": None},
                                {"ingestion_lease_until": {"$lte": now}},
                            ]
                        },
                    ],
                }
            )
            .sort("_id", ASCENDING)
            .limit(limit)
        )
        messages: list[tuple[PostId, TelegramTextMessage]] = []
        async for document in cursor:
            snapshot = await self._preparations.find_one(
                {
                    "_id": f"ingestion-message:{document['_id']}",
                    "ingestion_snapshot_expires_at": {"$gt": now},
                }
            )
            if snapshot is None:
                await self.acknowledge_ingestion(PostId(document["_id"]))
                continue
            payload = dict(snapshot["ingestion_message"])
            payload["source_channel_id"] = int(payload["source_channel_id"])
            payload["source_message_id"] = int(payload["source_message_id"])
            for name in ("text_entities", "caption_entities"):
                payload[name] = tuple(TelegramEntity(**item) for item in payload[name])
            payload["media"] = tuple(
                TelegramMediaReference(
                    **{**item, "media_type": MediaType(item["media_type"])}
                )
                for item in payload["media"]
            )
            payload["inline_keyboard"] = tuple(
                tuple(TelegramUrlButton(**item) for item in row)
                for row in payload.get("inline_keyboard", [])
            )
            messages.append((PostId(document["_id"]), TelegramTextMessage(**payload)))
        return tuple(messages)

    @_mongo_operation
    async def acknowledge_ingestion(self, post_id: PostId) -> None:
        """Clear durable recovery demand after a completed runtime operation."""
        await self._preparations.update_one(
            {"_id": post_id.value},
            {
                "$set": {
                    "recovery_requested": False,
                    "recovery_not_before": None,
                    "ingestion_completed": True,
                }
            },
        )

    @_mongo_operation
    async def get_preparation_state(self, post_id: PostId) -> PreparationState:
        """Read the small terminal-state projection without loading artifacts."""
        document = await self._preparations.find_one(
            {"_id": post_id.value},
            projection={
                "ready_at": 1,
                "outbox_recorded": 1,
                "media_rejected": 1,
                "recovery_requested": 1,
            },
        )
        if document is None:
            return PreparationState()
        return PreparationState(
            document.get("ready_at"),
            document.get("outbox_recorded", False),
            document.get("media_rejected", False),
            document.get("recovery_requested", False),
        )

    @_mongo_operation
    async def mark_outbox_recorded(self, post_id: PostId) -> None:
        """Acknowledge outbox creation only after its durable write succeeds."""
        await self._preparations.update_one(
            {"_id": post_id.value, "ready_at": {"$exists": True}},
            {"$set": {"outbox_recorded": True}},
        )

    @_mongo_operation
    async def claim_ingestion(
        self, post_id: PostId, *, owner: str, now: datetime, lease_until: datetime
    ) -> bool:
        """Acquire a per-Post lease without overwriting another live worker."""
        try:
            document = await self._preparations.find_one_and_update(
                {
                    "_id": post_id.value,
                    "$or": [
                        {"ingestion_owner": None},
                        {"ingestion_lease_until": {"$lte": now}},
                    ],
                },
                {
                    "$set": {
                        "ingestion_owner": owner,
                        "ingestion_lease_until": lease_until,
                    }
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return document is not None

    @_mongo_operation
    async def release_ingestion(self, post_id: PostId, *, owner: str) -> None:
        """Release only this worker's lease."""
        await self._preparations.update_one(
            {"_id": post_id.value, "ingestion_owner": owner},
            {"$unset": {"ingestion_owner": "", "ingestion_lease_until": ""}},
        )

    @_mongo_operation
    async def get_media_failure(self, identity: MediaIdentity) -> MediaFailure | None:
        """Load durable rejection independently of successful media metadata."""
        document = await self._preparations.find_one(
            {
                "_id": f"media-operation:{identity.key}",
                "media_operation.category": {"$ne": None},
            },
            projection={"media_operation": 1},
        )
        if document is None:
            return None
        operation = document["media_operation"]
        return MediaFailure(
            operation["category"],
            operation["attempts"],
            operation["failed_at"],
            operation.get("retry_at"),
            operation.get("maximum_bytes"),
            operation.get("size_bytes"),
            operation.get("last_failure_category"),
        )

    @_mongo_operation
    async def record_media_failure(
        self, spec: MediaDownloadSpec, failure: MediaFailure
    ) -> None:
        """Retain exact descriptors and block publication of rejected media."""
        operation = {
            "post_id": None if spec.post_id is None else spec.post_id.value,
            "source_channel_id": spec.identity.source_channel_id,
            "source_message_id": spec.identity.source_message_id,
            "item_index": spec.identity.item_index,
            "media_type": spec.media_type.value,
            "opaque_reference": spec.opaque_reference,
            "mime_type": spec.mime_type,
            "original_filename": spec.original_filename,
            "declared_size_bytes": spec.size_bytes,
            "media_group_id": spec.media_group_id,
            "category": failure.category,
            "attempts": failure.attempts,
            "failed_at": failure.failed_at,
            "retry_at": failure.retry_at,
            "maximum_bytes": failure.maximum_bytes,
            "size_bytes": failure.size_bytes,
            "last_failure_category": failure.last_failure_category,
        }
        await self._preparations.update_one(
            {"_id": f"media-operation:{spec.identity.key}"},
            {"$set": {"media_operation": operation}},
            upsert=True,
        )
        if failure.retry_at is not None and spec.post_id is not None:
            await self._preparations.update_one(
                {"_id": spec.post_id.value},
                {
                    "$set": {
                        "recovery_requested": True,
                        "recovery_not_before": failure.retry_at,
                    }
                },
                upsert=True,
            )
        if failure.category in TERMINAL_MEDIA_FAILURE_CATEGORIES:
            if spec.post_id is not None:
                await self._preparations.update_one(
                    {"_id": spec.post_id.value},
                    {"$set": {"media_rejected": True}},
                    upsert=True,
                )
            if spec.media_group_id is not None:
                await self._groups.update_one(
                    {
                        "_id": (
                            f"{spec.identity.source_channel_id}:{spec.media_group_id}"
                        ),
                        "finalized_at": None,
                    },
                    {
                        "$set": {
                            "finalization_status": (
                                AlbumFinalizationStatus.PERMANENT_FAILED.value
                            ),
                            "failure_category": "rejected_media_member",
                            "claim_owner": None,
                            "claim_expires_at": None,
                        }
                    },
                )

    @_mongo_operation
    async def clear_media_failure(self, identity: MediaIdentity) -> None:
        """Retain audit but clear only successfully recovered transient failures."""
        await self._preparations.update_one(
            {
                "_id": f"media-operation:{identity.key}",
                "media_operation.category": {"$nin": TERMINAL_MEDIA_FAILURE_CATEGORIES},
            },
            {
                "$set": {
                    "media_operation.category": None,
                    "media_operation.retry_at": None,
                }
            },
        )

    @_mongo_operation
    async def reset_media_rejections(
        self, post_id: PostId, *, administrator_id: int, at: datetime
    ) -> bool:
        """Reopen only explicitly requested rejected operations, with an audit."""
        query = {
            "media_operation.post_id": post_id.value,
            "media_operation.category": {"$in": TERMINAL_MEDIA_FAILURE_CATEGORIES},
        }
        operations = [document async for document in self._preparations.find(query)]
        for document in operations:
            operation = document["media_operation"]
            retry_at = operation.get("retry_at")
            if (
                operation.get("last_failure_category") == "rate_limit"
                and retry_at is not None
                and retry_at > at
            ):
                raise MediaRateLimitError(int((retry_at - at).total_seconds()) + 1)
        for document in operations:
            operation = document["media_operation"]
            await self._preparations.update_one(
                {
                    "_id": document["_id"],
                    "media_operation.category": operation["category"],
                },
                {
                    "$push": {
                        "media_retry_audit": {
                            "administrator_id": administrator_id,
                            "at": at,
                            "rejection": operation,
                        }
                    },
                    "$set": {
                        "media_operation.category": None,
                        "media_operation.retry_at": None,
                    },
                },
            )
            if operation.get("media_group_id") is not None:
                await self._groups.update_one(
                    {
                        "_id": (
                            f"{operation['source_channel_id']}:"
                            f"{operation['media_group_id']}"
                        ),
                        "finalized_at": None,
                        "finalization_status": (
                            AlbumFinalizationStatus.PERMANENT_FAILED.value
                        ),
                    },
                    {
                        "$set": {
                            "finalization_status": (
                                AlbumFinalizationStatus.PENDING.value
                            ),
                            "attempt_count": 0,
                            "next_attempt_at": None,
                            "failure_category": None,
                            "claim_owner": None,
                            "claim_expires_at": None,
                        }
                    },
                )
        result = await self._preparations.update_one(
            {"_id": post_id.value, "media_rejected": True},
            {
                "$set": {
                    "media_rejected": False,
                    "recovery_requested": True,
                    "ingestion_completed": False,
                    "recovery_not_before": None,
                }
            },
        )
        return bool(operations) or result.modified_count == 1

    @_mongo_operation
    async def get_media(self, identity: MediaIdentity) -> StoredMedia | None:
        """Load one non-cleaned media record."""
        document = await self._media.find_one({"_id": identity.key, "cleaned_at": None})
        return None if document is None else _media_from(document)

    @_mongo_operation
    async def save_media_if_absent(self, media: StoredMedia) -> StoredMedia:
        """Insert one identity or return its matching canonical record."""
        try:
            await self._media.insert_one(_media_document(media))
            return media
        except DuplicateKeyError:
            existing = await self._media.find_one({"_id": media.identity.key})
            if existing is None:
                raise
            canonical = _media_from(existing)
            if canonical.content_hash != media.content_hash:
                raise ValueError("Media identity has conflicting content.") from None
            return canonical

    @_mongo_operation
    async def list_media_for_preview(self) -> tuple[StoredMedia, ...]:
        """Load all current media records in deterministic identity order."""
        cursor = self._media.find({"cleaned_at": None}).sort("_id", ASCENDING)
        return tuple([_media_from(document) async for document in cursor])

    @_mongo_operation
    async def list_cleanup_candidates(
        self, *, now: datetime, orphan_before: datetime, limit: int
    ) -> tuple[StoredMedia, ...]:
        """List a bounded, starvation-free, deterministic expired-media batch.

        Candidate selection is an explicit two-class fairness policy:

        1. Expired candidates that never received a cleanup attempt (no
           `cleanup_next_check_at`) in `_id` order. Their query uses the existing
           deferral/retention indexes, so a bounded scan reads only expired media
           and never walks the much larger fresh-media population.
        2. Expired deferred candidates whose retry instant arrived, ordered by due
           instant and then `_id`. `ix_media_cleanup_fairness_v4`
           (`cleaned_at`, `cleanup_next_check_at`, `_id`) serves this query and its
           ordering directly, so a due page is fetched without a blocking sort.

        Never-attempted candidates always precede retries, so a bounded set of
        perpetually referenced (deferred) records can never permanently starve
        later expired, unreferenced records while batch size and worker cycle
        bounds stay unchanged. Each class keeps deterministic `_id` order, and the
        two classes are disjoint.
        """
        del orphan_before
        never_attempted = await self._cleanup_candidate_page(
            never_attempted_candidate_filter(now),
            sort=[("_id", ASCENDING)],
            limit=limit,
        )
        remaining = limit - len(never_attempted)
        if remaining <= 0:
            return never_attempted
        due_retries = await self._cleanup_candidate_page(
            due_retry_candidate_filter(now),
            sort=[("cleanup_next_check_at", ASCENDING), ("_id", ASCENDING)],
            limit=remaining,
        )
        return (*never_attempted, *due_retries)

    @_mongo_operation
    async def _cleanup_candidate_page(
        self, query: Document, *, sort: list[tuple[str, int]], limit: int
    ) -> tuple[StoredMedia, ...]:
        """Load one bounded deterministic page for one cleanup candidate class."""
        cursor = self._media.find(query).sort(sort).limit(limit)
        return tuple([_media_from(document) async for document in cursor])

    @_mongo_operation
    async def is_storage_path_referenced(
        self, storage_path: str, *, now: datetime
    ) -> bool:
        """Recheck shared files and every durable nonterminal consumer."""
        fresh = await self._media.find_one(
            {
                "storage_path": storage_path,
                "cleaned_at": None,
                "$or": [
                    {"media_expires_at": {"$gt": now}},
                    {
                        "media_expires_at": {"$exists": False},
                        "expires_at": {"$gt": now},
                    },
                ],
            },
            projection={"_id": 1},
        )
        if fresh is not None:
            return True

        cursor = self._media.find(
            {"storage_path": storage_path, "cleaned_at": None},
            projection={
                "post_id": 1,
                "source_channel_id": 1,
                "source_message_id": 1,
            },
        )
        documents = [document async for document in cursor]
        post_ids = {
            str(document["post_id"])
            for document in documents
            if isinstance(document.get("post_id"), str)
        }
        references = self._references
        if references.posts is not None:
            identities = [
                {
                    "source_channel_id": document.get("source_channel_id"),
                    "source_message_id": document.get("source_message_id"),
                }
                for document in documents
            ]
            if identities:
                post_cursor = references.posts.find(
                    {"$or": identities}, projection={"_id": 1}
                )
                async for item in post_cursor:
                    post_ids.add(str(item["_id"]))

        if (
            await self._groups.find_one(
                {
                    "members.media.storage_path": storage_path,
                    "$or": [
                        {"finalization_status": {"$exists": False}},
                        {
                            "finalization_status": {
                                "$in": [
                                    AlbumFinalizationStatus.PENDING.value,
                                    AlbumFinalizationStatus.PROCESSING.value,
                                ]
                            }
                        },
                    ],
                },
                projection={"_id": 1},
            )
            is not None
        ):
            return True

        if references.advertisement_sources is not None:
            snapshots = references.advertisement_sources
            if (
                await snapshots.find_one(
                    {
                        "media_references.storage_path": storage_path,
                        "is_current": True,
                    },
                    projection={"_id": 1},
                )
                is not None
            ):
                return True
            snapshot_cursor = snapshots.find(
                {"media_references.storage_path": storage_path},
                projection={"_id": 1},
            )
            snapshot_ids = [str(item["_id"]) async for item in snapshot_cursor]
            if (
                snapshot_ids
                and references.advertisement_slots is not None
                and await references.advertisement_slots.find_one(
                    {
                        "source_snapshot_id": {"$in": snapshot_ids},
                        "status": {
                            "$in": [
                                "scheduled",
                                "claimed",
                                "waiting_for_retry",
                            ]
                        },
                    },
                    projection={"_id": 1},
                )
                is not None
            ):
                return True

        if not post_ids:
            return False
        active_post_ids: set[str] = set()
        if references.posts is not None:
            active_cursor = references.posts.find(
                {
                    "_id": {"$in": sorted(post_ids)},
                    "expires_at": {"$gt": now},
                    "status": {"$ne": "Expired"},
                },
                projection={"_id": 1},
            )
            active_post_ids = {str(item["_id"]) async for item in active_cursor}
        post_filter = {"post_id": {"$in": sorted(post_ids)}}
        if (
            await self._preparations.find_one(
                {
                    "_id": {"$in": sorted(post_ids)},
                    "ready_at": {"$exists": False},
                    "ingestion_completed": {"$ne": True},
                },
                projection={"_id": 1},
            )
            is not None
        ):
            return True
        checks = (
            (
                references.publications,
                {
                    **post_filter,
                    "state": {"$in": ["Pending", "Claimed", "WaitingForRetry"]},
                },
            ),
            (
                references.schedules,
                {
                    **post_filter,
                    "status": {
                        "$in": ["Pending", "Claimed", "Running", "WaitingForRetry"]
                    },
                },
            ),
            (
                references.native_schedules,
                {
                    **post_filter,
                    "status": {
                        "$in": [
                            "pending",
                            "claimed",
                            "request_started",
                            "scheduled",
                            "cancel_requested",
                            "cancelling",
                        ]
                    },
                },
            ),
        )
        for collection, query in checks:
            if (
                collection is not None
                and await collection.find_one(query, projection={"_id": 1}) is not None
            ):
                return True
        approvals = references.approval_deliveries
        if approvals is not None:
            if (
                await approvals.find_one(
                    {
                        "_id": {"$in": sorted(post_ids)},
                        "status": {"$in": ["pending", "claimed", "retry"]},
                    },
                    projection={"_id": 1},
                )
                is not None
            ):
                return True
            if (
                active_post_ids
                and await approvals.find_one(
                    {
                        "_id": {"$in": sorted(active_post_ids)},
                        "status": "completed",
                        # A completed approval protects media only while its own
                        # approval lifecycle is still live. The approval cleanup
                        # lifecycle persists `approval_expired = True` once the
                        # administrator action window is over, and
                        # `is_actionable()` already treats that record as
                        # non-actionable. A missing field is legacy data that was
                        # never explicitly expired and therefore keeps protecting
                        # the file.
                        "approval_expired": {"$ne": True},
                    },
                    projection={"_id": 1},
                )
                is not None
            ):
                return True
        return False

    @_mongo_operation
    async def mark_media_cleaned(
        self, identity: MediaIdentity, *, cleaned_at: datetime
    ) -> bool:
        """Conditionally mark one media identity cleaned."""
        result = await self._media.update_one(
            {"_id": identity.key, "cleaned_at": None},
            {"$set": {"cleaned_at": cleaned_at}},
        )
        return result.modified_count == 1

    @_mongo_operation
    async def defer_media_cleanup(
        self, identity: MediaIdentity, *, until: datetime
    ) -> bool:
        """Push one non-cleaned candidate to a bounded future attempt."""
        result = await self._media.update_one(
            {"_id": identity.key, "cleaned_at": None},
            {"$set": {"cleanup_next_check_at": until}},
        )
        return result.modified_count == 1

    @_mongo_operation
    async def has_media_record_for_storage_path(self, storage_path: str) -> bool:
        """Return whether any active non-cleaned media record owns the path."""
        document = await self._media.find_one(
            {"storage_path": storage_path, "cleaned_at": None},
            projection={"_id": 1},
        )
        return document is not None

    @_mongo_operation
    async def add_group_member(
        self, group: MediaGroup, member: MediaGroupMember
    ) -> MediaGroup:
        """Atomically append one replay-safe group member."""
        member_document = {
            "source_message_id": member.source_message_id,
            "source_date": member.source_date,
            "media": _media_document(member.media),
            "caption": member.caption,
            "caption_entities": [
                _entity_document(item) for item in member.caption_entities
            ],
            "observed_at": member.observed_at,
            "telegram_group_id": member.telegram_group_id,
        }
        with contextlib.suppress(DuplicateKeyError):
            await self._groups.update_one(
                {
                    "_id": group.group_key,
                    "finalized_at": None,
                    "member_ids": {"$ne": member.source_message_id},
                },
                {
                    "$setOnInsert": {
                        "source_channel_id": group.source_channel_id,
                        "telegram_group_id": group.telegram_group_id,
                        "first_member_at": group.first_member_at,
                        "maximum_wait_until": group.maximum_wait_until,
                        "finalized_at": None,
                        "finalization_status": AlbumFinalizationStatus.PENDING.value,
                        "attempt_count": 0,
                        "next_attempt_at": None,
                        "claim_owner": None,
                        "claim_expires_at": None,
                        "failure_category": None,
                    },
                    "$set": {
                        "last_member_at": group.last_member_at,
                        "finalize_after": group.finalize_after,
                    },
                    "$push": {
                        "members": member_document,
                        "member_ids": member.source_message_id,
                    },
                    "$addToSet": {
                        "observed_message_ids": member.source_message_id,
                    },
                },
                upsert=True,
            )
        loaded = await self.get_group(group.group_key)
        if loaded is None:
            raise RuntimeError("Media group write did not produce a document.")
        return loaded

    @_mongo_operation
    async def observe_group_member(
        self,
        group: MediaGroup,
        *,
        source_message_id: int,
    ) -> MediaGroup:
        """Record arrival before download and extend the durable quiet window."""
        await self._groups.update_one(
            {"_id": group.group_key, "finalized_at": None},
            {
                "$setOnInsert": {
                    "source_channel_id": group.source_channel_id,
                    "telegram_group_id": group.telegram_group_id,
                    "first_member_at": group.first_member_at,
                    "maximum_wait_until": group.maximum_wait_until,
                    "members": [],
                    "member_ids": [],
                    "finalized_at": None,
                    "finalization_status": AlbumFinalizationStatus.PENDING.value,
                    "attempt_count": 0,
                    "next_attempt_at": None,
                    "claim_owner": None,
                    "claim_expires_at": None,
                    "failure_category": None,
                },
                "$set": {
                    "last_member_at": group.last_member_at,
                    "finalize_after": group.finalize_after,
                },
                "$addToSet": {"observed_message_ids": source_message_id},
            },
            upsert=True,
        )
        loaded = await self.get_group(group.group_key)
        if loaded is None:
            raise RuntimeError("Media group observation was not persisted.")
        return loaded

    @_mongo_operation
    async def get_group(self, group_key: str) -> MediaGroup | None:
        """Load and deterministically order a durable group."""
        document = await self._groups.find_one({"_id": group_key})
        return None if document is None else _group_from_document(document)

    @_mongo_operation
    async def finalize_group(self, group_key: str, *, at: datetime) -> bool:
        """Conditionally finalize one due group."""
        group = await self.get_group(group_key)
        if group is None or at < min(group.finalize_after, group.maximum_wait_until):
            return False
        result = await self._groups.update_one(
            {"_id": group_key, "finalized_at": None},
            {
                "$set": {
                    "finalized_at": at,
                    "finalization_status": AlbumFinalizationStatus.COMPLETED.value,
                }
            },
        )
        return result.modified_count == 1

    @_mongo_operation
    async def list_due_groups(
        self, *, now: datetime, limit: int
    ) -> tuple[MediaGroup, ...]:
        """Load a bounded deterministic batch whose persisted quiet window elapsed."""
        cursor = (
            self._groups.find(
                {
                    "finalized_at": None,
                    "finalize_after": {"$lte": now},
                    "finalization_status": {
                        "$nin": [
                            AlbumFinalizationStatus.COMPLETED.value,
                            AlbumFinalizationStatus.PERMANENT_FAILED.value,
                        ]
                    },
                    "$or": [
                        {"next_attempt_at": None},
                        {"next_attempt_at": {"$lte": now}},
                        {"next_attempt_at": {"$exists": False}},
                    ],
                }
            )
            .sort([("finalize_after", ASCENDING), ("_id", ASCENDING)])
            .limit(limit)
        )
        groups: list[MediaGroup] = []
        async for document in cursor:
            loaded = await self.get_group(str(document["_id"]))
            if loaded is not None:
                groups.append(loaded)
        return tuple(groups)

    @_mongo_operation
    async def claim_due_group(
        self,
        *,
        now: datetime,
        owner: str,
        lease_until: datetime,
    ) -> MediaGroup | None:
        """Atomically claim one due group, including legacy and expired claims."""
        held_groups: list[Document] = []
        async for document in self._preparations.find(
            {"history_hold": True},
            projection={"history_source_channel_id": 1, "history_snapshot_end": 1},
        ):
            held_group: Document = {
                "source_channel_id": document["history_source_channel_id"],
            }
            snapshot_end = document.get("history_snapshot_end")
            if snapshot_end is not None:
                held_group["$or"] = [
                    {"members.source_date": {"$lt": snapshot_end}},
                    {"members": {"$size": 0}},
                    {"members": {"$exists": False}},
                ]
            held_groups.append(held_group)
        query: Document = {
            "finalize_after": {"$lte": now},
            "$and": [
                {
                    "$or": [
                        {"finalized_at": None},
                        {"finalized_at": {"$exists": False}},
                    ]
                },
                {
                    "$or": [
                        {"finalization_status": {"$exists": False}},
                        {
                            "finalization_status": {
                                "$in": [
                                    AlbumFinalizationStatus.PENDING.value,
                                    AlbumFinalizationStatus.PROCESSING.value,
                                ]
                            }
                        },
                    ]
                },
                {
                    "$or": [
                        {"next_attempt_at": None},
                        {"next_attempt_at": {"$lte": now}},
                        {"next_attempt_at": {"$exists": False}},
                    ]
                },
                {
                    "$or": [
                        {"claim_owner": None},
                        {"claim_owner": {"$exists": False}},
                        {"claim_expires_at": {"$lte": now}},
                    ]
                },
            ],
        }
        if held_groups:
            query["$nor"] = held_groups
        claimed_document = await self._groups.find_one_and_update(
            query,
            {
                "$set": {
                    "finalization_status": AlbumFinalizationStatus.PROCESSING.value,
                    "claim_owner": owner,
                    "claim_expires_at": lease_until,
                    "last_attempt_at": now,
                },
                "$inc": {"attempt_count": 1},
            },
            sort=[("finalize_after", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return (
            None if claimed_document is None else _group_from_document(claimed_document)
        )

    @_mongo_operation
    async def complete_group_finalization(
        self,
        group_key: str,
        *,
        owner: str,
        at: datetime,
        canonical_source_message_id: int,
    ) -> bool:
        """Complete one owned group without changing unrelated records."""
        result = await self._groups.update_one(
            {
                "_id": group_key,
                "claim_owner": owner,
                "finalization_status": AlbumFinalizationStatus.PROCESSING.value,
            },
            {
                "$set": {
                    "finalized_at": at,
                    "finalization_status": AlbumFinalizationStatus.COMPLETED.value,
                    "canonical_source_message_id": canonical_source_message_id,
                    "next_attempt_at": None,
                    "failure_category": None,
                    "claim_owner": None,
                    "claim_expires_at": None,
                }
            },
        )
        return result.modified_count == 1

    @_mongo_operation
    async def defer_group_finalization(
        self,
        group_key: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        failure_category: str,
    ) -> bool:
        """Release one owned group for its next bounded retry."""
        result = await self._groups.update_one(
            {"_id": group_key, "claim_owner": owner},
            {
                "$set": {
                    "finalization_status": AlbumFinalizationStatus.PENDING.value,
                    "next_attempt_at": next_attempt_at,
                    "failure_category": failure_category,
                    "claim_owner": None,
                    "claim_expires_at": None,
                }
            },
        )
        return result.modified_count == 1

    @_mongo_operation
    async def fail_group_finalization(
        self,
        group_key: str,
        *,
        owner: str,
        at: datetime,
        failure_category: str,
    ) -> bool:
        """Permanently fail one owned malformed group without deleting it."""
        result = await self._groups.update_one(
            {"_id": group_key, "claim_owner": owner},
            {
                "$set": {
                    "finalization_status": (
                        AlbumFinalizationStatus.PERMANENT_FAILED.value
                    ),
                    "permanent_failed_at": at,
                    "next_attempt_at": None,
                    "failure_category": failure_category,
                    "claim_owner": None,
                    "claim_expires_at": None,
                }
            },
        )
        return result.modified_count == 1

    @_mongo_operation
    async def find_duplicate(
        self, *, content_hash: str, post_id: PostId, since: datetime
    ) -> PostId | None:
        """Find one exact hash match inside the supplied window."""
        document = await self._preparations.find_one(
            {
                "_id": {"$ne": post_id.value},
                "duplicate_result.content_hash": content_hash,
                "duplicate_result.checked_at": {"$gte": since},
            },
            projection={"_id": 1},
            sort=[("duplicate_result.checked_at", ASCENDING), ("_id", ASCENDING)],
        )
        return None if document is None else PostId(document["_id"])

    @_mongo_operation
    async def save_duplicate_result(
        self, post_id: PostId, result: DuplicateCheckResult
    ) -> DuplicateCheckResult:
        """Persist or return the canonical duplicate result."""
        payload = {
            "is_duplicate": result.is_duplicate,
            "matched_post_id": None
            if result.matched_post_id is None
            else result.matched_post_id.value,
            "method": result.method,
            "normalization_version": result.normalization_version,
            "hash_version": result.hash_version,
            "content_hash": result.content_hash,
            "checked_at": result.checked_at,
        }
        try:
            document = await self._preparations.find_one_and_update(
                {"_id": post_id.value, "duplicate_result": {"$exists": False}},
                {"$set": {"duplicate_result": payload}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            document = None
        if document is None:
            document = cast(
                "Document", await self._preparations.find_one({"_id": post_id.value})
            )
        current = document["duplicate_result"]
        return DuplicateCheckResult(
            current["is_duplicate"],
            None
            if current["matched_post_id"] is None
            else PostId(current["matched_post_id"]),
            current["method"],
            current["normalization_version"],
            current["hash_version"],
            current["content_hash"],
            current["checked_at"],
        )

    @_mongo_operation
    async def get_duplicate_result(
        self, post_id: PostId
    ) -> DuplicateCheckResult | None:
        """Load a completed duplicate result when present."""
        document = await self._preparations.find_one(
            {"_id": post_id.value}, projection={"duplicate_result": 1}
        )
        if document is None or "duplicate_result" not in document:
            return None
        current = document["duplicate_result"]
        return DuplicateCheckResult(
            current["is_duplicate"],
            None
            if current["matched_post_id"] is None
            else PostId(current["matched_post_id"]),
            current["method"],
            current["normalization_version"],
            current["hash_version"],
            current["content_hash"],
            current["checked_at"],
        )

    @_mongo_operation
    async def save_category_result(
        self, post_id: PostId, result: CategorizationResult
    ) -> CategorizationResult:
        """Persist category without overwriting a manual assignment."""
        payload = {
            "category_id": result.category_id,
            "method": result.method.value,
            "policy_version": result.policy_version,
            "assigned_at": result.assigned_at,
            "rule_id": result.rule_id,
            "reason": result.reason,
            "confidence": result.confidence,
            "provider_name": result.provider_name,
            "model_name": result.model_name,
            "prompt_version": result.prompt_version,
            "schema_version": result.schema_version,
            "cache_hit": result.cache_hit,
            "cache_age": result.cache_age,
            "attempt_number": result.attempt_number,
            "fallback_count": result.fallback_count,
        }
        query: Document = {"_id": post_id.value}
        if result.method is not CategorizationMethod.MANUAL:
            query["category_result.method"] = {"$ne": CategorizationMethod.MANUAL.value}
        with contextlib.suppress(DuplicateKeyError):
            await self._preparations.update_one(
                query, {"$set": {"category_result": payload}}, upsert=True
            )
        document = cast(
            "Document", await self._preparations.find_one({"_id": post_id.value})
        )
        current = document["category_result"]
        return CategorizationResult(
            category_id=current["category_id"],
            method=CategorizationMethod(current["method"]),
            policy_version=current["policy_version"],
            assigned_at=current["assigned_at"],
            rule_id=current.get("rule_id"),
            reason=current.get("reason"),
            confidence=current.get("confidence"),
            provider_name=current.get("provider_name"),
            model_name=current.get("model_name"),
            prompt_version=current.get("prompt_version"),
            schema_version=current.get("schema_version"),
            cache_hit=current.get("cache_hit"),
            cache_age=current.get("cache_age"),
            attempt_number=current.get("attempt_number"),
            fallback_count=current.get("fallback_count"),
        )

    @_mongo_operation
    async def get_category_result(self, post_id: PostId) -> CategorizationResult | None:
        """Load a completed category result when present."""
        document = await self._preparations.find_one(
            {"_id": post_id.value}, projection={"category_result": 1}
        )
        if document is None or "category_result" not in document:
            return None
        current = document["category_result"]
        return CategorizationResult(
            category_id=current["category_id"],
            method=CategorizationMethod(current["method"]),
            policy_version=current["policy_version"],
            assigned_at=current["assigned_at"],
            rule_id=current.get("rule_id"),
            reason=current.get("reason"),
            confidence=current.get("confidence"),
            provider_name=current.get("provider_name"),
            model_name=current.get("model_name"),
            prompt_version=current.get("prompt_version"),
            schema_version=current.get("schema_version"),
            cache_hit=current.get("cache_hit"),
            cache_age=current.get("cache_age"),
            attempt_number=current.get("attempt_number"),
            fallback_count=current.get("fallback_count"),
        )

    @_mongo_operation
    async def save_destination_artifact(
        self, artifact: DestinationArtifact
    ) -> DestinationArtifact:
        """Persist one canonical artifact per destination."""
        key = f"artifacts.{artifact.destination_id}"
        payload = {
            "text": artifact.text,
            "entities": [_entity_document(item) for item in artifact.entities],
            "content_policy_version": artifact.content_policy_version,
        }
        with contextlib.suppress(DuplicateKeyError):
            await self._preparations.update_one(
                {"_id": artifact.post_id.value, key: {"$exists": False}},
                {"$set": {key: payload}},
                upsert=True,
            )
        document = cast(
            "Document",
            await self._preparations.find_one({"_id": artifact.post_id.value}),
        )
        current = document["artifacts"][artifact.destination_id]
        return DestinationArtifact(
            artifact.post_id,
            artifact.destination_id,
            current["text"],
            tuple(_entity_from(item) for item in current["entities"]),
            current["content_policy_version"],
        )

    @_mongo_operation
    async def get_destination_artifact(
        self, post_id: PostId, destination_id: str
    ) -> DestinationArtifact | None:
        """Load one completed destination artifact when present."""
        document = await self._preparations.find_one(
            {"_id": post_id.value}, projection={f"artifacts.{destination_id}": 1}
        )
        if document is None or destination_id not in document.get("artifacts", {}):
            return None
        current = document["artifacts"][destination_id]
        return DestinationArtifact(
            post_id,
            destination_id,
            current["text"],
            tuple(_entity_from(item) for item in current["entities"]),
            current["content_policy_version"],
        )

    @_mongo_operation
    async def mark_preparation_ready(
        self, post_id: PostId, *, at: datetime
    ) -> datetime | None:
        """Atomically create readiness exactly once and return its durable time.

        The returned timestamp is the canonical durable readiness instant of the
        preparation. It equals `at` for the transition created by this call and the
        previously stored instant when readiness already existed, so a caller can
        create the matching approval-outbox identity without re-reading or
        rewriting the readiness marker.
        """
        try:
            result = await self._preparations.update_one(
                {"_id": post_id.value, "ready_at": {"$exists": False}},
                {"$set": {"ready_at": at}},
                upsert=True,
            )
        except DuplicateKeyError:
            return await self._stored_readiness(post_id)
        if result.modified_count == 1 or result.upserted_id is not None:
            return at
        return await self._stored_readiness(post_id)

    @_mongo_operation
    async def _stored_readiness(self, post_id: PostId) -> datetime | None:
        """Load the stored readiness instant of one preparation when present."""
        document = await self._preparations.find_one(
            {"_id": post_id.value}, projection={"ready_at": 1}
        )
        if document is None:
            return None
        ready_at = document.get("ready_at")
        return ready_at if type(ready_at) is datetime else None
