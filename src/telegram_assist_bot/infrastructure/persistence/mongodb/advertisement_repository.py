"""MongoDB implementation of AdvertisementRepository for versioned snapshots."""

from __future__ import annotations

import hashlib
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ports import (
    AdvertisementRepository,
)
from telegram_assist_bot.application.ports.advertisement_repository import (
    AdvertisementReportKind,
    AdvertisementReportQuery,
    AdvertisementReportRecord,
    AdvertisementSlotRepository,
)
from telegram_assist_bot.domain.advertisement_slot import (
    AdvertisementCollisionAudit,
    AdvertisementSlot,
    AdvertisementSlotAudit,
    AdvertisementSlotStatus,
)
from telegram_assist_bot.domain.advertisement_source import (
    AdvertisementMediaReference,
    AdvertisementSourceIdentity,
    AdvertisementSourceSnapshot,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.domain.publication_collision import CollisionResolutionState

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )


def _aware_utc(value: datetime) -> datetime:
    """Normalize MongoDB's possibly naive UTC datetime to an aware instant."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _entity_to_dict(entity: TelegramEntity) -> dict[str, Any]:
    return {
        "offset_utf16": entity.offset_utf16,
        "length_utf16": entity.length_utf16,
        "entity_type": entity.entity_type,
        "custom_emoji_id": entity.custom_emoji_id,
        "url": entity.url,
    }


def _dict_to_entity(data: dict[str, Any]) -> TelegramEntity:
    return TelegramEntity(
        offset_utf16=data["offset_utf16"],
        length_utf16=data["length_utf16"],
        entity_type=data["entity_type"],
        custom_emoji_id=data.get("custom_emoji_id"),
        url=data.get("url"),
    )


def _media_to_dict(ref: AdvertisementMediaReference) -> dict[str, Any]:
    return {
        "media_type": str(ref.media_type),
        "item_index": ref.item_index,
        "size_bytes": ref.size_bytes,
        "mime_type": ref.mime_type,
        "original_filename": ref.original_filename,
        "storage_path": ref.storage_path,
        "media_group_id": ref.media_group_id,
    }


def _dict_to_media(data: dict[str, Any]) -> AdvertisementMediaReference:
    storage_path = data.get("storage_path") or data.get("opaque_reference")
    if not isinstance(storage_path, str):
        raise ValueError("advertisement media storage path is missing")
    size_bytes = data.get("size_bytes")
    if type(size_bytes) is not int:
        raise ValueError("advertisement media size is missing")
    return AdvertisementMediaReference(
        media_type=MediaType(data["media_type"]),
        item_index=data["item_index"],
        size_bytes=size_bytes,
        mime_type=data.get("mime_type"),
        original_filename=data.get("original_filename"),
        storage_path=storage_path,
        media_group_id=data.get("media_group_id"),
    )


def _snapshot_to_doc(snapshot: AdvertisementSourceSnapshot) -> dict[str, Any]:
    return {
        "_id": snapshot.snapshot_id,
        "snapshot_id": snapshot.snapshot_id,
        "campaign_id": snapshot.campaign_id,
        "source_identity": {
            "campaign_id": snapshot.source_identity.campaign_id,
            "source_channel_username": snapshot.source_identity.source_channel_username,
            "source_message_id": snapshot.source_identity.source_message_id,
            "source_identity_fingerprint": (
                snapshot.source_identity.source_identity_fingerprint
            ),
        },
        "source_identity_fingerprint": (
            snapshot.source_identity.source_identity_fingerprint
        ),
        "snapshot_version": snapshot.snapshot_version,
        "snapshot_contract_version": snapshot.snapshot_contract_version,
        "content_hash": snapshot.content_hash,
        "text": snapshot.text,
        "caption": snapshot.caption,
        "text_entities": [_entity_to_dict(e) for e in snapshot.text_entities],
        "caption_entities": [_entity_to_dict(e) for e in snapshot.caption_entities],
        "media_group_id": snapshot.media_group_id,
        "media_references": [_media_to_dict(m) for m in snapshot.media_references],
        "source_published_at": snapshot.source_published_at,
        "source_edited_at": snapshot.source_edited_at,
        "fetched_at": snapshot.fetched_at,
        "last_successful_fetch_at": snapshot.last_successful_fetch_at,
        "is_current": snapshot.is_current,
        "expires_at": snapshot.expires_at,
        "document_type": "snapshot",
        "is_stale": snapshot.is_stale,
        "stale_reason": snapshot.stale_reason,
    }


def _doc_to_snapshot(doc: dict[str, Any]) -> AdvertisementSourceSnapshot:
    id_doc = doc["source_identity"]
    identity = AdvertisementSourceIdentity(
        campaign_id=id_doc["campaign_id"],
        source_channel_username=id_doc["source_channel_username"],
        source_message_id=id_doc["source_message_id"],
        source_identity_fingerprint=id_doc["source_identity_fingerprint"],
    )
    pub_at = doc["source_published_at"]
    if pub_at.tzinfo is None:
        pub_at = pub_at.replace(tzinfo=UTC)
    edit_at = doc.get("source_edited_at")
    if edit_at is not None and edit_at.tzinfo is None:
        edit_at = edit_at.replace(tzinfo=UTC)
    fetch_at = doc["fetched_at"]
    if fetch_at.tzinfo is None:
        fetch_at = fetch_at.replace(tzinfo=UTC)
    last_success_at = doc["last_successful_fetch_at"]
    if last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=UTC)
    exp_at = doc.get("expires_at")
    if exp_at is not None and exp_at.tzinfo is None:
        exp_at = exp_at.replace(tzinfo=UTC)

    return AdvertisementSourceSnapshot(
        snapshot_id=doc["snapshot_id"],
        campaign_id=doc["campaign_id"],
        source_identity=identity,
        snapshot_version=doc["snapshot_version"],
        snapshot_contract_version=doc["snapshot_contract_version"],
        content_hash=doc["content_hash"],
        text=doc.get("text"),
        caption=doc.get("caption"),
        text_entities=tuple(_dict_to_entity(e) for e in doc.get("text_entities", ())),
        caption_entities=tuple(
            _dict_to_entity(e) for e in doc.get("caption_entities", ())
        ),
        media_group_id=doc.get("media_group_id"),
        media_references=tuple(
            _dict_to_media(m) for m in doc.get("media_references", ())
        ),
        source_published_at=pub_at,
        source_edited_at=edit_at,
        fetched_at=fetch_at,
        last_successful_fetch_at=last_success_at,
        is_current=doc.get("is_current", True),
        expires_at=exp_at,
        is_stale=doc.get("is_stale", False),
        stale_reason=doc.get("stale_reason"),
    )


class MongoAdvertisementRepository(AdvertisementRepository):
    """MongoDB implementation of AdvertisementRepository."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Initialize repository with an AsyncCollection instance."""
        self._collection = collection

    async def initialize_indexes(self) -> None:
        """Initialize MongoDB collection indexes idempotently."""
        await self._collection.create_index(
            [
                ("campaign_id", 1),
                ("source_identity_fingerprint", 1),
                ("snapshot_version", 1),
            ],
            unique=True,
            name="idx_campaign_source_version",
        )
        await self._collection.create_index(
            [
                ("campaign_id", 1),
                ("source_identity_fingerprint", 1),
                ("is_current", 1),
            ],
            unique=True,
            partialFilterExpression={"is_current": True},
            name="idx_campaign_source_current",
        )
        await self._collection.create_index(
            [("expires_at", 1)],
            expireAfterSeconds=0,
            name="idx_snapshot_expires_at_ttl",
        )

    async def get_current_snapshot(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
    ) -> AdvertisementSourceSnapshot | None:
        """Return the current active snapshot for a campaign source identity."""
        doc = await self._collection.find_one(
            {
                "campaign_id": campaign_id,
                "source_identity_fingerprint": source_identity_fingerprint,
                "is_current": True,
            }
        )
        if doc is None:
            return None
        return _doc_to_snapshot(doc)

    async def get_snapshot_by_id(
        self, snapshot_id: str
    ) -> AdvertisementSourceSnapshot | None:
        """Return an exact current or historical snapshot by identity."""
        doc = await self._collection.find_one({"snapshot_id": snapshot_id})
        return None if doc is None else _doc_to_snapshot(doc)

    async def save_initial_snapshot(
        self,
        snapshot: AdvertisementSourceSnapshot,
    ) -> AdvertisementSourceSnapshot:
        """Persist the initial snapshot for a campaign source identity."""
        doc = _snapshot_to_doc(snapshot)
        try:
            await self._collection.insert_one(doc)
            return snapshot
        except DuplicateKeyError:
            current = await self.get_current_snapshot(
                snapshot.campaign_id,
                snapshot.source_identity.source_identity_fingerprint,
            )
            if current is not None:
                return current
            raise

    async def commit_changed_snapshot(
        self,
        new_snapshot: AdvertisementSourceSnapshot,
        expected_current_version: int,
        retention_days: int,
    ) -> AdvertisementSourceSnapshot:
        """CAS-replace the current snapshot and retain its immutable prior version."""
        now = new_snapshot.fetched_at
        expiry = now + timedelta(days=retention_days)
        replacement = _snapshot_to_doc(new_snapshot)
        replacement.pop("_id", None)
        previous = await self._collection.find_one_and_update(
            {
                "campaign_id": new_snapshot.campaign_id,
                "source_identity_fingerprint": (
                    new_snapshot.source_identity.source_identity_fingerprint
                ),
                "snapshot_version": expected_current_version,
                "is_current": True,
            },
            {"$set": replacement},
            return_document=ReturnDocument.BEFORE,
        )
        if previous is None:
            current = await self.get_current_snapshot(
                new_snapshot.campaign_id,
                new_snapshot.source_identity.source_identity_fingerprint,
            )
            if current is not None:
                return current
            raise RuntimeError("advertisement snapshot compare-and-set conflict")

        previous["_id"] = f"{previous['snapshot_id']}:history"
        previous["is_current"] = False
        previous["expires_at"] = expiry
        with suppress(DuplicateKeyError):
            await self._collection.insert_one(previous)
        return new_snapshot

    async def record_unchanged_check(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
        fetched_at: datetime,
    ) -> None:
        """Update last_successful_fetch_at timestamp for current snapshot atomically."""
        await self._collection.update_one(
            {
                "campaign_id": campaign_id,
                "source_identity_fingerprint": source_identity_fingerprint,
                "is_current": True,
            },
            {
                "$set": {
                    "fetched_at": fetched_at,
                    "last_successful_fetch_at": fetched_at,
                }
            },
        )

    async def record_failed_check(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
        failed_at: datetime,
        error_reason: str,
    ) -> None:
        """Record sanitized failure audit without replacing current content."""
        safe_reason = (
            error_reason
            if error_reason
            in {"source_deleted", "permanently_unavailable", "temporarily_unavailable"}
            else "source_unavailable"
        )
        await self._collection.update_one(
            {
                "campaign_id": campaign_id,
                "source_identity_fingerprint": source_identity_fingerprint,
                "is_current": True,
            },
            {
                "$set": {
                    "last_failed_fetch_at": failed_at,
                    "last_fetch_failure_reason": safe_reason,
                },
                "$inc": {"failed_fetch_count": 1},
            },
        )


def _slot_to_doc(slot: AdvertisementSlot) -> dict[str, Any]:
    return {
        "_id": slot.slot_id,
        "slot_id": slot.slot_id,
        "campaign_id": slot.campaign_id,
        "destination_name": slot.destination_name,
        "destination_id": slot.destination_id,
        "due_at": slot.due_at,
        "local_scheduled_value": slot.local_scheduled_at.isoformat(),
        "timezone_name": slot.timezone_name,
        "source_snapshot_id": slot.source_snapshot_id,
        "source_snapshot_version": slot.source_snapshot_version,
        "config_fingerprint": slot.config_fingerprint,
        "priority": slot.priority,
        "minimum_gap_seconds": slot.minimum_gap_seconds,
        "max_retries": slot.max_retries,
        "created_at": slot.created_at,
        "updated_at": slot.updated_at,
        "status": slot.status.value,
        "version": slot.version,
        "claim_owner": slot.claim_owner,
        "lease_until": slot.lease_until,
        "claim_count": slot.claim_count,
        "publication_attempt_count": slot.publication_attempt_count,
        "next_attempt_at": slot.next_attempt_at,
        "publication_id": slot.publication_id,
        "message_ids": list(slot.message_ids),
        "published_at": slot.published_at,
        "execution_delay_seconds": slot.execution_delay_seconds,
        "last_error_category": slot.last_error_category,
        "last_failure_type": slot.last_failure_type,
        "last_failure_reason_code": slot.last_failure_reason_code,
        "effective_due_at": slot.effective_due_at,
        "collision_state": slot.collision_state.value,
        "collision_history": [
            {
                "old_due_at": item.old_due_at,
                "new_due_at": item.new_due_at,
                "policy_version": item.policy_version,
                "reason": item.reason,
                "occurred_at": item.occurred_at,
            }
            for item in slot.collision_history
        ],
        "immutable_collision_ids": list(slot.immutable_collision_ids),
        "document_type": "advertisement_slot",
    }


def _doc_to_slot(doc: dict[str, Any]) -> AdvertisementSlot:
    def aware(value: datetime) -> datetime:
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value

    return AdvertisementSlot(
        slot_id=doc["slot_id"],
        campaign_id=doc["campaign_id"],
        destination_name=doc["destination_name"],
        destination_id=doc["destination_id"],
        due_at=aware(doc["due_at"]),
        local_scheduled_at=datetime.fromisoformat(doc["local_scheduled_value"]),
        timezone_name=doc["timezone_name"],
        source_snapshot_id=doc["source_snapshot_id"],
        source_snapshot_version=doc["source_snapshot_version"],
        config_fingerprint=doc["config_fingerprint"],
        priority=doc["priority"],
        minimum_gap_seconds=doc["minimum_gap_seconds"],
        max_retries=doc["max_retries"],
        created_at=aware(doc["created_at"]),
        updated_at=aware(doc["updated_at"]),
        status=AdvertisementSlotStatus(doc["status"]),
        version=doc.get("version", 0),
        claim_owner=doc.get("claim_owner"),
        lease_until=(
            aware(doc["lease_until"]) if doc.get("lease_until") is not None else None
        ),
        claim_count=doc.get("claim_count", 0),
        publication_attempt_count=doc.get("publication_attempt_count", 0),
        next_attempt_at=(
            aware(doc["next_attempt_at"])
            if doc.get("next_attempt_at") is not None
            else None
        ),
        publication_id=doc.get("publication_id"),
        message_ids=tuple(doc.get("message_ids", ())),
        published_at=(
            aware(doc["published_at"]) if doc.get("published_at") is not None else None
        ),
        execution_delay_seconds=doc.get("execution_delay_seconds"),
        last_error_category=doc.get("last_error_category"),
        last_failure_type=doc.get("last_failure_type"),
        last_failure_reason_code=doc.get("last_failure_reason_code"),
        effective_due_at=aware(doc.get("effective_due_at") or doc["due_at"]),
        collision_state=CollisionResolutionState(
            doc.get("collision_state", CollisionResolutionState.UNRESOLVED.value)
        ),
        collision_history=tuple(
            AdvertisementCollisionAudit(
                old_due_at=aware(item["old_due_at"]),
                new_due_at=aware(item["new_due_at"]),
                policy_version=item["policy_version"],
                reason=item["reason"],
                occurred_at=aware(item["occurred_at"]),
            )
            for item in doc.get("collision_history", ())
        ),
        immutable_collision_ids=tuple(doc.get("immutable_collision_ids", ())),
    )


class MongoAdvertisementSlotRepository(AdvertisementSlotRepository):
    """MongoDB repository for durable advertisement slot reconciliation."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Store the dedicated advertisement-slot collection."""
        self._collection = collection

    async def initialize_indexes(self) -> None:
        """Create slot identity, due-status, and campaign indexes idempotently."""
        await self._collection.create_index(
            [("slot_id", 1)], unique=True, name="uq_advertisement_slot_identity"
        )
        await self._collection.create_index(
            [("status", 1), ("due_at", 1), ("destination_id", 1)],
            name="ix_advertisement_slot_due",
        )
        await self._collection.create_index(
            [("status", 1), ("collision_state", 1), ("effective_due_at", 1)],
            name="ix_advertisement_slot_collision_claim_v2",
        )
        await self._collection.create_index(
            [("campaign_id", 1), ("due_at", 1), ("destination_id", 1)],
            name="ix_advertisement_slot_campaign",
        )
        await self._collection.create_index(
            [
                ("document_type", 1),
                ("destination_id", 1),
                ("effective_due_at", 1),
                ("destination_name", 1),
                ("campaign_id", 1),
            ],
            name="ix_advertisement_report_schedule",
        )
        await self._collection.create_index(
            [
                ("document_type", 1),
                ("status", 1),
                ("destination_id", 1),
                ("updated_at", -1),
                ("campaign_id", 1),
            ],
            name="ix_advertisement_report_failure",
        )

    async def list_report_records(
        self, query: AdvertisementReportQuery
    ) -> tuple[AdvertisementReportRecord, ...]:
        """Return a minimal bounded projection for one authorized report."""
        base: dict[str, Any] = {
            "document_type": "advertisement_slot",
            "destination_id": {"$in": sorted(query.allowed_destination_ids)},
        }
        if query.kind is AdvertisementReportKind.FAILURES:
            base.update(
                {
                    "status": {
                        "$in": [
                            AdvertisementSlotStatus.WAITING_FOR_RETRY.value,
                            AdvertisementSlotStatus.PERMANENT_FAILED.value,
                            AdvertisementSlotStatus.OUTCOME_UNKNOWN.value,
                        ]
                    },
                    "updated_at": {"$gte": query.starts_at, "$lte": query.ends_at},
                }
            )
            sort = [
                ("updated_at", -1),
                ("campaign_id", 1),
                ("destination_name", 1),
                ("_id", 1),
            ]
        else:
            base["effective_due_at"] = {
                "$gte": query.starts_at,
                "$lt": query.ends_at,
            }
            if query.kind is AdvertisementReportKind.UPCOMING:
                base["status"] = {
                    "$in": [
                        AdvertisementSlotStatus.SCHEDULED.value,
                        AdvertisementSlotStatus.CLAIMED.value,
                        AdvertisementSlotStatus.WAITING_FOR_RETRY.value,
                    ]
                }
            else:
                base["status"] = {
                    "$ne": AdvertisementSlotStatus.CANCELLED_BY_RECONCILIATION.value
                }
            sort = [
                ("effective_due_at", 1),
                ("destination_name", 1),
                ("campaign_id", 1),
                ("_id", 1),
            ]
        projection = {
            "slot_id": 1,
            "campaign_id": 1,
            "destination_name": 1,
            "destination_id": 1,
            "status": 1,
            "effective_due_at": 1,
            "due_at": 1,
            "published_at": 1,
            "message_ids": 1,
            "publication_attempt_count": 1,
            "claim_count": 1,
            "execution_delay_seconds": 1,
            "last_error_category": 1,
            "last_failure_reason_code": 1,
            "updated_at": 1,
        }
        cursor = self._collection.find(base, projection).sort(sort).limit(query.limit)
        records: list[AdvertisementReportRecord] = []
        async for doc in cursor:
            scheduled_at = doc.get("effective_due_at") or doc["due_at"]
            published_at = doc.get("published_at")
            updated_at = doc.get("updated_at")
            record_id = doc["slot_id"]
            campaign_id = doc["campaign_id"]
            destination_name = doc["destination_name"]
            destination_id = doc["destination_id"]
            status = doc["status"]
            message_ids = doc.get("message_ids", ())
            attempt_count = doc.get("publication_attempt_count", 0)
            claim_count = doc.get("claim_count", 0)
            delay = doc.get("execution_delay_seconds")
            failure_category = doc.get("last_error_category")
            failure_reason = doc.get("last_failure_reason_code")
            if not (
                isinstance(record_id, str)
                and isinstance(campaign_id, str)
                and isinstance(destination_name, str)
                and type(destination_id) is int
                and isinstance(status, str)
                and isinstance(scheduled_at, datetime)
                and isinstance(message_ids, (list, tuple))
                and all(type(value) is int for value in message_ids)
                and type(attempt_count) is int
                and type(claim_count) is int
                and (delay is None or type(delay) in (int, float))
                and (failure_category is None or isinstance(failure_category, str))
                and (failure_reason is None or isinstance(failure_reason, str))
            ):
                raise ValueError("invalid advertisement report projection")
            records.append(
                AdvertisementReportRecord(
                    record_id=record_id,
                    campaign_id=campaign_id,
                    destination_name=destination_name,
                    destination_id=destination_id,
                    status=status,
                    scheduled_at=_aware_utc(scheduled_at),
                    published_at=(
                        _aware_utc(published_at)
                        if isinstance(published_at, datetime)
                        else None
                    ),
                    message_ids=tuple(cast("list[int] | tuple[int, ...]", message_ids)),
                    retry_count=max(
                        attempt_count,
                        max(0, claim_count - 1),
                    ),
                    execution_delay_seconds=(
                        float(cast("int | float", delay)) if delay is not None else None
                    ),
                    failure_category=failure_category,
                    failure_reason_code=failure_reason,
                    latest_failure_at=(
                        _aware_utc(updated_at)
                        if query.kind is AdvertisementReportKind.FAILURES
                        and isinstance(updated_at, datetime)
                        else None
                    ),
                )
            )
        return tuple(records)

    async def reconcile_campaign_slots(
        self,
        campaign_id: str,
        desired_slots: tuple[AdvertisementSlot, ...],
        audits: tuple[AdvertisementSlotAudit, ...],
        *,
        now: datetime,
    ) -> tuple[AdvertisementSlot, ...]:
        """Upsert desired identities and cancel only obsolete future slots."""
        desired_ids = [slot.slot_id for slot in desired_slots]
        await self._collection.update_many(
            {
                "document_type": "advertisement_slot",
                "campaign_id": campaign_id,
                "due_at": {"$gte": now},
                "status": AdvertisementSlotStatus.SCHEDULED.value,
                "slot_id": {"$nin": desired_ids},
            },
            {
                "$set": {
                    "status": (
                        AdvertisementSlotStatus.CANCELLED_BY_RECONCILIATION.value
                    ),
                    "updated_at": now,
                },
                "$inc": {"version": 1},
            },
        )
        for slot in desired_slots:
            update = {
                "destination_name": slot.destination_name,
                "timezone_name": slot.timezone_name,
                "local_scheduled_value": slot.local_scheduled_at.isoformat(),
                "source_snapshot_id": slot.source_snapshot_id,
                "source_snapshot_version": slot.source_snapshot_version,
                "config_fingerprint": slot.config_fingerprint,
                "priority": slot.priority,
                "minimum_gap_seconds": slot.minimum_gap_seconds,
                "max_retries": slot.max_retries,
                "status": AdvertisementSlotStatus.SCHEDULED.value,
                "effective_due_at": slot.due_at,
                "collision_state": CollisionResolutionState.UNRESOLVED.value,
                "collision_history": [],
                "immutable_collision_ids": [],
                "updated_at": now,
            }
            changed = await self._collection.update_one(
                {
                    "_id": slot.slot_id,
                    "due_at": {"$gte": now},
                    "status": {
                        "$in": [
                            AdvertisementSlotStatus.SCHEDULED.value,
                            AdvertisementSlotStatus.CANCELLED_BY_RECONCILIATION.value,
                        ]
                    },
                    "$or": [
                        {
                            "status": (
                                AdvertisementSlotStatus.CANCELLED_BY_RECONCILIATION.value
                            )
                        },
                        {"config_fingerprint": {"$ne": slot.config_fingerprint}},
                        {"source_snapshot_id": {"$ne": slot.source_snapshot_id}},
                        {
                            "source_snapshot_version": {
                                "$ne": slot.source_snapshot_version
                            }
                        },
                    ],
                },
                {"$set": update, "$inc": {"version": 1}},
            )
            if changed.matched_count:
                continue
            with suppress(DuplicateKeyError):
                await self._collection.insert_one(_slot_to_doc(slot))

        for audit in audits:
            raw = (
                f"{audit.campaign_id}:{audit.timezone_name}:"
                f"{audit.local_scheduled_value}:{audit.reason}"
            )
            audit_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()
            await self._collection.update_one(
                {"_id": f"audit:{audit_id}"},
                {
                    "$setOnInsert": {
                        "campaign_id": audit.campaign_id,
                        "local_scheduled_value": audit.local_scheduled_value,
                        "timezone_name": audit.timezone_name,
                        "reason": audit.reason,
                        "recorded_at": audit.recorded_at,
                        "document_type": "advertisement_slot_audit",
                    }
                },
                upsert=True,
            )
        return await self.list_campaign_slots(campaign_id)

    async def list_campaign_slots(
        self, campaign_id: str
    ) -> tuple[AdvertisementSlot, ...]:
        """Return campaign slots deterministically by due time and destination."""
        cursor = self._collection.find(
            {"campaign_id": campaign_id, "document_type": "advertisement_slot"}
        ).sort([("due_at", 1), ("destination_id", 1), ("slot_id", 1)])
        return tuple([_doc_to_slot(doc) async for doc in cursor])

    async def claim_due_slot(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
    ) -> AdvertisementSlot | None:
        """Claim the oldest due slot with expired-lease recovery."""
        doc = await self._collection.find_one_and_update(
            {
                "document_type": "advertisement_slot",
                "effective_due_at": {"$lte": now},
                "collision_state": CollisionResolutionState.RESOLVED.value,
                "$or": [
                    {"status": AdvertisementSlotStatus.SCHEDULED.value},
                    {
                        "status": AdvertisementSlotStatus.WAITING_FOR_RETRY.value,
                        "next_attempt_at": {"$lte": now},
                    },
                    {
                        "status": AdvertisementSlotStatus.CLAIMED.value,
                        "lease_until": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    "status": AdvertisementSlotStatus.CLAIMED.value,
                    "claim_owner": owner,
                    "lease_until": lease_until,
                    "next_attempt_at": None,
                    "updated_at": now,
                },
                "$inc": {"claim_count": 1, "version": 1},
            },
            sort=[("effective_due_at", 1), ("slot_id", 1)],
            return_document=ReturnDocument.AFTER,
        )
        return None if doc is None else _doc_to_slot(doc)

    async def complete_slot(
        self,
        slot_id: str,
        *,
        owner: str,
        expected_version: int,
        publication_id: str,
        publication_attempt_count: int,
        message_ids: tuple[int, ...],
        published_at: datetime,
    ) -> AdvertisementSlot | None:
        """Complete one exact owned slot version and persist safe audit fields."""
        existing = await self._collection.find_one({"_id": slot_id})
        if existing is None:
            return None
        stored_due_at = existing.get("due_at")
        if not isinstance(stored_due_at, datetime):
            raise ValueError("advertisement slot due_at must be a datetime")
        due_at = (
            stored_due_at.replace(tzinfo=UTC)
            if stored_due_at.tzinfo is None
            else stored_due_at.astimezone(UTC)
        )
        delay = max(0.0, (published_at.astimezone(UTC) - due_at).total_seconds())
        doc = await self._collection.find_one_and_update(
            {
                "_id": slot_id,
                "status": AdvertisementSlotStatus.CLAIMED.value,
                "claim_owner": owner,
                "version": expected_version,
            },
            {
                "$set": {
                    "status": AdvertisementSlotStatus.COMPLETED.value,
                    "publication_id": publication_id,
                    "publication_attempt_count": publication_attempt_count,
                    "message_ids": list(message_ids),
                    "published_at": published_at,
                    "execution_delay_seconds": delay,
                    "claim_owner": None,
                    "lease_until": None,
                    "updated_at": published_at,
                    "last_error_category": None,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        return None if doc is None else _doc_to_slot(doc)

    async def defer_slot(
        self,
        slot_id: str,
        *,
        owner: str,
        expected_version: int,
        next_attempt_at: datetime,
        category: str,
        failure_type: str | None,
        reason_code: str | None,
    ) -> AdvertisementSlot | None:
        """Persist an independently retryable owned slot state."""
        doc = await self._collection.find_one_and_update(
            {
                "_id": slot_id,
                "status": AdvertisementSlotStatus.CLAIMED.value,
                "claim_owner": owner,
                "version": expected_version,
            },
            {
                "$set": {
                    "status": AdvertisementSlotStatus.WAITING_FOR_RETRY.value,
                    "next_attempt_at": next_attempt_at,
                    "last_error_category": category,
                    "last_failure_type": failure_type,
                    "last_failure_reason_code": reason_code,
                    "claim_owner": None,
                    "lease_until": None,
                    "updated_at": next_attempt_at,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        return None if doc is None else _doc_to_slot(doc)

    async def fail_slot(
        self,
        slot_id: str,
        *,
        owner: str,
        expected_version: int,
        publication_attempt_count: int,
        category: str,
        failure_type: str | None,
        reason_code: str | None,
        outcome_unknown: bool,
    ) -> AdvertisementSlot | None:
        """Persist a terminal safe failure or ambiguous outcome."""
        status = (
            AdvertisementSlotStatus.OUTCOME_UNKNOWN
            if outcome_unknown
            else AdvertisementSlotStatus.PERMANENT_FAILED
        )
        doc = await self._collection.find_one_and_update(
            {
                "_id": slot_id,
                "status": AdvertisementSlotStatus.CLAIMED.value,
                "claim_owner": owner,
                "version": expected_version,
            },
            {
                "$set": {
                    "status": status.value,
                    "publication_attempt_count": publication_attempt_count,
                    "last_error_category": category,
                    "last_failure_type": failure_type,
                    "last_failure_reason_code": reason_code,
                    "claim_owner": None,
                    "lease_until": None,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        return None if doc is None else _doc_to_slot(doc)
