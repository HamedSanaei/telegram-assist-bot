"""MongoDB atomic publication claims and durable destination schedules."""

from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ports import (
    PublicationClaimOutcome,
    PublicationClaimResult,
    ScheduleReservation,
)
from telegram_assist_bot.domain import (
    CancellationPolicy,
    CancellationResult,
    DueTimeAudit,
    Publication,
    PublicationFailureCategory,
    PublicationState,
    PublishedMessage,
    ScheduledPublication,
    ScheduleStatus,
)

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


async def initialize_publication_indexes(
    publications: AsyncCollection[Document],
    schedules: AsyncCollection[Document],
    queues: AsyncCollection[Document],
) -> None:
    """Create the restart-safe Milestone 4 identity and claim indexes."""
    await publications.create_index(
        [("post_id", ASCENDING), ("destination_id", ASCENDING), ("action", ASCENDING)],
        unique=True,
        name="uq_publication_action_v1",
    )
    await publications.create_index(
        [
            ("state", ASCENDING),
            ("next_attempt_at", ASCENDING),
            ("lease_until", ASCENDING),
        ],
        name="ix_publication_claim_v1",
    )
    await schedules.create_index(
        [("post_id", ASCENDING), ("destination_id", ASCENDING), ("action", ASCENDING)],
        unique=True,
        name="uq_schedule_action_v1",
    )
    await schedules.create_index(
        [("destination_id", ASCENDING), ("due_at", ASCENDING)],
        name="ix_schedule_destination_due_v1",
    )
    await schedules.create_index(
        [
            ("status", ASCENDING),
            ("due_at", ASCENDING),
            ("next_attempt_at", ASCENDING),
            ("lease_until", ASCENDING),
        ],
        name="ix_schedule_claim_v1",
    )
    # MongoDB's built-in unique ``_id`` index is the queue identity primitive.
    del queues


def _publication(document: Document) -> Publication:
    return Publication(
        publication_id=document["_id"],
        post_id=document["post_id"],
        destination_id=document["destination_id"],
        state=PublicationState(document["state"]),
        version=document.get("version", 0),
        claim_owner=document.get("claim_owner"),
        lease_until=document.get("lease_until"),
        attempt_count=document.get("attempt_count", 0),
        attempted_at=document.get("attempted_at"),
        next_attempt_at=document.get("next_attempt_at"),
        message_ids=tuple(document.get("message_ids", ())),
        published_at=document.get("published_at"),
        error_category=document.get("error_category"),
        correlation_id=document.get("correlation_id"),
        failure_type=document.get("failure_type"),
    )


def _schedule(document: Document) -> ScheduledPublication:
    history = tuple(
        DueTimeAudit(
            item["old_due_at"],
            item["new_due_at"],
            item["policy_version"],
            item["actor_id"],
            item["occurred_at"],
            item["correlation_id"],
        )
        for item in document.get("due_time_history", ())
    )
    return ScheduledPublication(
        job_id=document["_id"],
        post_id=document["post_id"],
        destination_id=document["destination_id"],
        due_at=document["due_at"],
        status=ScheduleStatus(document["status"]),
        version=document.get("version", 0),
        claim_owner=document.get("claim_owner"),
        lease_until=document.get("lease_until"),
        attempt_count=document.get("attempt_count", 0),
        next_attempt_at=document.get("next_attempt_at"),
        publication_id=document.get("publication_id"),
        completed_at=document.get("completed_at"),
        last_error_category=document.get("last_error_category"),
        due_time_history=history,
        action=document.get("action", "scheduled"),
        last_failure_type=document.get("last_failure_type"),
    )


class MongoPublicationRepository:
    """Implement atomic publication claims without leaking driver values."""

    def __init__(self, collection: AsyncCollection[Document]) -> None:
        """Store the concrete publication collection."""
        self._collection = collection

    async def claim(
        self,
        *,
        publication_id: str,
        post_id: str,
        destination_id: int,
        owner: str,
        now: datetime,
        lease_until: datetime,
        max_attempts: int,
        correlation_id: str,
        action: str = "immediate",
    ) -> PublicationClaimResult:
        """Insert identity once then atomically claim an eligible attempt."""
        with suppress(DuplicateKeyError):
            await self._collection.insert_one(
                {
                    "_id": publication_id,
                    "post_id": post_id,
                    "destination_id": destination_id,
                    "action": action,
                    "state": PublicationState.PENDING.value,
                    "version": 0,
                    "attempt_count": 0,
                    "correlation_id": correlation_id,
                }
            )
        document = await self._collection.find_one_and_update(
            {
                "_id": publication_id,
                "attempt_count": {"$lt": max_attempts},
                "$or": [
                    {"state": PublicationState.PENDING.value},
                    {
                        "state": PublicationState.WAITING_FOR_RETRY.value,
                        "next_attempt_at": {"$lte": now},
                    },
                    {
                        "state": PublicationState.CLAIMED.value,
                        "lease_until": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    "state": PublicationState.CLAIMED.value,
                    "claim_owner": owner,
                    "lease_until": lease_until,
                    "attempted_at": now,
                    "next_attempt_at": None,
                },
                "$inc": {"attempt_count": 1, "version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is not None:
            return PublicationClaimResult(
                PublicationClaimOutcome.CLAIMED, _publication(document)
            )
        existing = await self._collection.find_one({"_id": publication_id})
        if existing is None:
            raise RuntimeError("Publication identity disappeared during claim.")
        publication = _publication(existing)
        if publication.state in {
            PublicationState.SUCCEEDED,
            PublicationState.PERMANENT_FAILED,
            PublicationState.OUTCOME_UNKNOWN,
        }:
            return PublicationClaimResult(PublicationClaimOutcome.TERMINAL, publication)
        outcome = (
            PublicationClaimOutcome.EXHAUSTED
            if publication.attempt_count >= max_attempts
            else PublicationClaimOutcome.BUSY
        )
        return PublicationClaimResult(outcome, publication)

    async def complete(
        self, publication_id: str, *, owner: str, result: PublishedMessage
    ) -> Publication:
        """Conditionally persist all destination message identifiers."""
        document = await self._collection.find_one_and_update(
            {
                "_id": publication_id,
                "state": PublicationState.CLAIMED.value,
                "claim_owner": owner,
            },
            {
                "$set": {
                    "state": PublicationState.SUCCEEDED.value,
                    "message_ids": list(result.message_ids),
                    "published_at": result.published_at,
                    "claim_owner": None,
                    "lease_until": None,
                    "error_category": None,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise RuntimeError("Publication lease was lost before completion.")
        return _publication(document)

    async def fail(
        self,
        publication_id: str,
        *,
        owner: str,
        category: PublicationFailureCategory,
        now: datetime,
        next_attempt_at: datetime | None,
        outcome_unknown: bool,
        failure_type: str | None = None,
    ) -> Publication:
        """Persist only a safe category and never raw exception details."""
        state = (
            PublicationState.OUTCOME_UNKNOWN
            if outcome_unknown
            else PublicationState.WAITING_FOR_RETRY
            if next_attempt_at is not None
            else PublicationState.PERMANENT_FAILED
        )
        document = await self._collection.find_one_and_update(
            {
                "_id": publication_id,
                "state": PublicationState.CLAIMED.value,
                "claim_owner": owner,
            },
            {
                "$set": {
                    "state": state.value,
                    "error_category": category.value,
                    "failure_type": failure_type,
                    "next_attempt_at": next_attempt_at,
                    "claim_owner": None,
                    "lease_until": None,
                    "attempted_at": now,
                },
                "$inc": {"version": 1},
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise RuntimeError("Publication lease was lost before failure persistence.")
        return _publication(document)


class MongoScheduleRepository:
    """Implement durable per-destination slot reservation and worker leases."""

    def __init__(
        self, schedules: AsyncCollection[Document], queues: AsyncCollection[Document]
    ) -> None:
        """Store schedule and per-destination queue collections."""
        self._schedules = schedules
        self._queues = queues

    async def reserve(
        self,
        *,
        job_id: str,
        post_id: str,
        destination_id: int,
        now: datetime,
        interval: timedelta,
    ) -> ScheduleReservation:
        """Return an existing identity or atomically advance its destination queue."""
        existing = await self._schedules.find_one({"_id": job_id})
        if existing is not None:
            return ScheduleReservation(_schedule(existing), False)
        seconds = interval.total_seconds()
        queue = await self._queues.find_one_and_update(
            {"_id": destination_id},
            [
                {
                    "$set": {
                        "_reservation_slots": {"$ifNull": ["$slots", []]},
                    }
                },
                {
                    "$set": {
                        "_reservation_existing": {
                            "$filter": {
                                "input": "$_reservation_slots",
                                "as": "slot",
                                "cond": {"$eq": ["$$slot.job_id", job_id]},
                            }
                        }
                    }
                },
                {
                    "$set": {
                        "_reservation_next_due": {
                            "$add": [
                                {
                                    "$cond": [
                                        {
                                            "$gt": [
                                                {"$ifNull": ["$last_due_at", now]},
                                                now,
                                            ]
                                        },
                                        {"$ifNull": ["$last_due_at", now]},
                                        now,
                                    ]
                                },
                                seconds * 1000,
                            ]
                        }
                    }
                },
                {
                    "$set": {
                        "last_due_at": {
                            "$cond": [
                                {"$gt": [{"$size": "$_reservation_existing"}, 0]},
                                "$last_due_at",
                                "$_reservation_next_due",
                            ]
                        },
                        "slots": {
                            "$cond": [
                                {"$gt": [{"$size": "$_reservation_existing"}, 0]},
                                "$_reservation_slots",
                                {
                                    "$concatArrays": [
                                        "$_reservation_slots",
                                        [
                                            {
                                                "job_id": job_id,
                                                "due_at": "$_reservation_next_due",
                                            }
                                        ],
                                    ]
                                },
                            ]
                        },
                    }
                },
                {
                    "$unset": [
                        "_reservation_slots",
                        "_reservation_existing",
                        "_reservation_next_due",
                    ]
                },
            ],
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        if queue is None:
            raise RuntimeError("Schedule queue reservation failed.")
        reservation = next(item for item in queue["slots"] if item["job_id"] == job_id)
        due_at = reservation["due_at"]
        document = {
            "_id": job_id,
            "post_id": post_id,
            "destination_id": destination_id,
            "action": "scheduled",
            "due_at": due_at,
            "status": ScheduleStatus.PENDING.value,
            "version": 0,
            "attempt_count": 0,
        }
        try:
            await self._schedules.insert_one(document)
        except DuplicateKeyError:
            canonical = await self._schedules.find_one({"_id": job_id})
            if canonical is None:
                raise
            return ScheduleReservation(_schedule(canonical), False)
        return ScheduleReservation(_schedule(document), True)

    async def reserve_immediate(
        self,
        *,
        job_id: str,
        post_id: str,
        destination_id: int,
        now: datetime,
    ) -> ScheduleReservation:
        """Insert one due-now command without occupying a scheduled queue slot."""
        document = {
            "_id": job_id,
            "post_id": post_id,
            "destination_id": destination_id,
            "action": "immediate",
            "due_at": now,
            "status": ScheduleStatus.PENDING.value,
            "version": 0,
            "attempt_count": 0,
        }
        try:
            await self._schedules.insert_one(document)
        except DuplicateKeyError:
            existing = await self._schedules.find_one({"_id": job_id})
            if existing is None:
                raise
            return ScheduleReservation(_schedule(existing), False)
        return ScheduleReservation(_schedule(document), True)

    async def get(self, job_id: str) -> ScheduledPublication | None:
        """Load one publication command by deterministic identity."""
        document = await self._schedules.find_one({"_id": job_id})
        return None if document is None else _schedule(document)

    async def claim_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        action: str = "scheduled",
    ) -> ScheduledPublication | None:
        """Claim the globally oldest eligible due job."""
        document = await self._schedules.find_one_and_update(
            {
                "action": action,
                "due_at": {"$lte": now},
                "$or": [
                    {"status": ScheduleStatus.PENDING.value},
                    {
                        "status": ScheduleStatus.WAITING_FOR_RETRY.value,
                        "next_attempt_at": {"$lte": now},
                    },
                    {
                        "status": {
                            "$in": [
                                ScheduleStatus.CLAIMED.value,
                                ScheduleStatus.RUNNING.value,
                            ]
                        },
                        "lease_until": {"$lte": now},
                    },
                ],
            },
            {
                "$set": {
                    "status": ScheduleStatus.CLAIMED.value,
                    "claim_owner": owner,
                    "lease_until": lease_until,
                    "next_attempt_at": None,
                },
                "$inc": {"attempt_count": 1, "version": 1},
            },
            sort=[("due_at", ASCENDING), ("_id", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else _schedule(document)

    async def complete(self, job_id: str, *, owner: str, at: datetime) -> bool:
        """Complete an owned claim exactly once."""
        result = await self._schedules.update_one(
            {
                "_id": job_id,
                "status": ScheduleStatus.CLAIMED.value,
                "claim_owner": owner,
            },
            {
                "$set": {
                    "status": ScheduleStatus.COMPLETED.value,
                    "completed_at": at,
                    "claim_owner": None,
                    "lease_until": None,
                },
                "$inc": {"version": 1},
            },
        )
        return result.modified_count == 1

    async def defer(
        self,
        job_id: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        category: str,
        failure_type: str | None = None,
    ) -> bool:
        """Release an owned claim into retry waiting."""
        result = await self._schedules.update_one(
            {
                "_id": job_id,
                "status": ScheduleStatus.CLAIMED.value,
                "claim_owner": owner,
            },
            {
                "$set": {
                    "status": ScheduleStatus.WAITING_FOR_RETRY.value,
                    "next_attempt_at": next_attempt_at,
                    "last_error_category": category,
                    "last_failure_type": failure_type,
                    "claim_owner": None,
                    "lease_until": None,
                },
                "$inc": {"version": 1},
            },
        )
        return result.modified_count == 1

    async def fail(
        self,
        job_id: str,
        *,
        owner: str,
        category: str,
        failure_type: str | None = None,
    ) -> bool:
        """Persist a terminal safe failure category."""
        state = (
            ScheduleStatus.OUTCOME_UNKNOWN
            if category == "ambiguous"
            else ScheduleStatus.PERMANENT_FAILED
        )
        result = await self._schedules.update_one(
            {
                "_id": job_id,
                "status": ScheduleStatus.CLAIMED.value,
                "claim_owner": owner,
            },
            {
                "$set": {
                    "status": state.value,
                    "last_error_category": category,
                    "last_failure_type": failure_type,
                    "claim_owner": None,
                    "lease_until": None,
                },
                "$inc": {"version": 1},
            },
        )
        return result.modified_count == 1

    async def cancel(
        self,
        *,
        job_id: str,
        destination_id: int,
        expected_version: int,
        policy: CancellationPolicy,
        interval: timedelta,
        actor_id: int,
        now: datetime,
        correlation_id: str,
    ) -> CancellationResult:
        """Conditionally cancel and apply the configured queue policy."""
        document = await self._schedules.find_one(
            {"_id": job_id, "destination_id": destination_id}
        )
        if document is None:
            return CancellationResult.NOT_FOUND
        status = ScheduleStatus(document["status"])
        if status is ScheduleStatus.CANCELLED:
            return CancellationResult.ALREADY_CANCELLED
        if status is ScheduleStatus.COMPLETED:
            return CancellationResult.ALREADY_COMPLETED
        if status in {ScheduleStatus.CLAIMED, ScheduleStatus.RUNNING}:
            return CancellationResult.ALREADY_EXECUTING
        if status not in {ScheduleStatus.PENDING, ScheduleStatus.WAITING_FOR_RETRY}:
            return CancellationResult.INVALID_STATE
        result = await self._schedules.update_one(
            {
                "_id": job_id,
                "destination_id": destination_id,
                "version": expected_version,
                "status": {
                    "$in": [
                        ScheduleStatus.PENDING.value,
                        ScheduleStatus.WAITING_FOR_RETRY.value,
                    ]
                },
            },
            {
                "$set": {
                    "status": ScheduleStatus.CANCELLED.value,
                    "cancelled_at": now,
                    "cancelled_by": actor_id,
                },
                "$inc": {"version": 1},
            },
        )
        if result.modified_count != 1:
            return CancellationResult.CONFLICT
        if (
            policy is CancellationPolicy.RECOMPACT
            and document.get("action", "scheduled") == "scheduled"
        ):
            cursor = self._schedules.find(
                {
                    "destination_id": destination_id,
                    "action": "scheduled",
                    "due_at": {"$gt": document["due_at"]},
                    "status": {
                        "$in": [
                            ScheduleStatus.PENDING.value,
                            ScheduleStatus.WAITING_FOR_RETRY.value,
                        ]
                    },
                }
            ).sort("due_at", ASCENDING)
            previous = document["due_at"] - interval
            async for later in cursor:
                new_due = previous + interval
                audit = {
                    "old_due_at": later["due_at"],
                    "new_due_at": new_due,
                    "policy_version": 1,
                    "actor_id": actor_id,
                    "occurred_at": now,
                    "correlation_id": correlation_id,
                }
                changed = await self._schedules.update_one(
                    {
                        "_id": later["_id"],
                        "version": later.get("version", 0),
                        "status": later["status"],
                    },
                    {
                        "$set": {"due_at": new_due},
                        "$push": {"due_time_history": audit},
                        "$inc": {"version": 1},
                    },
                )
                if changed.modified_count != 1:
                    return CancellationResult.CONFLICT
                previous = new_due
            await self._queues.update_one(
                {"_id": destination_id}, {"$set": {"last_due_at": previous}}
            )
        return CancellationResult.CANCELLED


__all__ = (
    "MongoPublicationRepository",
    "MongoScheduleRepository",
    "initialize_publication_indexes",
)
