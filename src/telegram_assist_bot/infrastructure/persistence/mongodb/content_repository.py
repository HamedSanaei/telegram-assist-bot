"""MongoDB adapter for Milestone 2 preparation metadata and atomic results."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any, cast

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ports import (
    AlbumFinalizationStatus,
    DestinationArtifact,
    InvalidMediaGroupRecordError,
    MediaGroup,
    MediaGroupMember,
)
from telegram_assist_bot.domain.categories import (
    CategorizationMethod,
    CategorizationResult,
)
from telegram_assist_bot.domain.duplicates import DuplicateCheckResult
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.domain.posts import PostId, TelegramEntity

if TYPE_CHECKING:
    from datetime import datetime

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


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
        "expires_at": media.expires_at,
        "cleaned_at": None,
    }


def _media_from(document: Document) -> StoredMedia:
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
        document["expires_at"],
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
    ) -> None:
        """Initialize concrete MongoDB collections."""
        self._media, self._groups, self._preparations = media, groups, preparations

    async def get_media(self, identity: MediaIdentity) -> StoredMedia | None:
        """Load one non-cleaned media record."""
        document = await self._media.find_one({"_id": identity.key, "cleaned_at": None})
        return None if document is None else _media_from(document)

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

    async def list_media_for_preview(self) -> tuple[StoredMedia, ...]:
        """Load all current media records in deterministic identity order."""
        cursor = self._media.find({"cleaned_at": None}).sort("_id", ASCENDING)
        return tuple([_media_from(document) async for document in cursor])

    async def list_cleanup_candidates(
        self, *, now: datetime, orphan_before: datetime, limit: int
    ) -> tuple[StoredMedia, ...]:
        """List a bounded deterministic expired-media batch."""
        del orphan_before
        cursor = (
            self._media.find({"cleaned_at": None, "expires_at": {"$lte": now}})
            .sort("_id", ASCENDING)
            .limit(limit)
        )
        items = [_media_from(document) async for document in cursor]
        return tuple(items)

    async def is_storage_path_referenced(
        self, storage_path: str, *, now: datetime
    ) -> bool:
        """Recheck whether any non-expired record references a path."""
        return (
            await self._media.find_one(
                {
                    "storage_path": storage_path,
                    "expires_at": {"$gt": now},
                    "cleaned_at": None,
                },
                projection={"_id": 1},
            )
            is not None
        )

    async def mark_media_cleaned(
        self, identity: MediaIdentity, *, cleaned_at: datetime
    ) -> bool:
        """Conditionally mark one media identity cleaned."""
        result = await self._media.update_one(
            {"_id": identity.key, "cleaned_at": None},
            {"$set": {"cleaned_at": cleaned_at}},
        )
        return result.modified_count == 1

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

    async def get_group(self, group_key: str) -> MediaGroup | None:
        """Load and deterministically order a durable group."""
        document = await self._groups.find_one({"_id": group_key})
        return None if document is None else _group_from_document(document)

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

    async def claim_due_group(
        self,
        *,
        now: datetime,
        owner: str,
        lease_until: datetime,
    ) -> MediaGroup | None:
        """Atomically claim one due group, including legacy and expired claims."""
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
        document = await self._groups.find_one_and_update(
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
        return None if document is None else _group_from_document(document)

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
            current["category_id"],
            CategorizationMethod(current["method"]),
            current["policy_version"],
            current["assigned_at"],
            current.get("rule_id"),
            current.get("reason"),
        )

    async def get_category_result(self, post_id: PostId) -> CategorizationResult | None:
        """Load a completed category result when present."""
        document = await self._preparations.find_one(
            {"_id": post_id.value}, projection={"category_result": 1}
        )
        if document is None or "category_result" not in document:
            return None
        current = document["category_result"]
        return CategorizationResult(
            current["category_id"],
            CategorizationMethod(current["method"]),
            current["policy_version"],
            current["assigned_at"],
            current.get("rule_id"),
            current.get("reason"),
        )

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

    async def mark_preparation_ready(self, post_id: PostId, *, at: datetime) -> bool:
        """Atomically create readiness exactly once."""
        try:
            result = await self._preparations.update_one(
                {"_id": post_id.value, "ready_at": {"$exists": False}},
                {"$set": {"ready_at": at}},
                upsert=True,
            )
        except DuplicateKeyError:
            return False
        return result.modified_count == 1 or result.upserted_id is not None
