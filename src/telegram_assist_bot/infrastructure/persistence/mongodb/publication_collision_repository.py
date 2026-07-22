"""MongoDB CAS adapter for advertisement/normal queue collision resolution."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from telegram_assist_bot.application.ports.publication_collision import (
    CollisionApplyOutcome,
    PublicationCollisionSnapshot,
)
from telegram_assist_bot.domain.advertisement_slot import AdvertisementSlotStatus
from telegram_assist_bot.domain.publication_collision import (
    CollisionAdvertisement,
    CollisionNormalPublication,
    CollisionResolutionState,
    PublicationCollisionPlan,
)
from telegram_assist_bot.domain.scheduling import ScheduleStatus

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]

_MOVABLE_ADVERTISEMENT_STATES = (
    AdvertisementSlotStatus.SCHEDULED.value,
    AdvertisementSlotStatus.WAITING_FOR_RETRY.value,
)
_MOVABLE_NORMAL_STATES = (
    ScheduleStatus.PENDING.value,
    ScheduleStatus.WAITING_FOR_RETRY.value,
)
_VISIBLE_ADVERTISEMENT_STATES = (
    *_MOVABLE_ADVERTISEMENT_STATES,
    AdvertisementSlotStatus.CLAIMED.value,
    AdvertisementSlotStatus.COMPLETED.value,
)
_VISIBLE_NORMAL_STATES = (
    *_MOVABLE_NORMAL_STATES,
    ScheduleStatus.CLAIMED.value,
    ScheduleStatus.RUNNING.value,
    ScheduleStatus.COMPLETED.value,
)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class MongoPublicationCollisionRepository:
    """Coordinate collision metadata across existing durable collections."""

    def __init__(
        self,
        advertisement_slots: AsyncCollection[Document],
        schedules: AsyncCollection[Document],
        schedule_queues: AsyncCollection[Document],
    ) -> None:
        """Store the existing advertisement, schedule, and queue collections."""
        self._advertisements = advertisement_slots
        self._schedules = schedules
        self._queues = schedule_queues

    async def load_destination(
        self, destination_id: int
    ) -> PublicationCollisionSnapshot:
        """Load minimum projections in deterministic order without content."""
        advertisement_cursor = self._advertisements.find(
            {
                "document_type": "advertisement_slot",
                "destination_id": destination_id,
                "status": {"$in": list(_VISIBLE_ADVERTISEMENT_STATES)},
            },
            {
                "slot_id": 1,
                "campaign_id": 1,
                "due_at": 1,
                "effective_due_at": 1,
                "minimum_gap_seconds": 1,
                "priority": 1,
                "version": 1,
                "status": 1,
                "collision_state": 1,
            },
        ).sort([("due_at", 1), ("slot_id", 1)])
        advertisements = tuple(
            [
                CollisionAdvertisement(
                    slot_id=document["slot_id"],
                    campaign_id=document["campaign_id"],
                    original_due_at=_aware(document["due_at"]),
                    current_due_at=_aware(
                        document.get("effective_due_at") or document["due_at"]
                    ),
                    minimum_gap_seconds=document["minimum_gap_seconds"],
                    priority=document["priority"],
                    version=document.get("version", 0),
                    movable=(
                        document["status"] in _MOVABLE_ADVERTISEMENT_STATES
                        and document.get("collision_state")
                        != CollisionResolutionState.RESOLVED.value
                    ),
                    resolved=(
                        document.get("collision_state")
                        == CollisionResolutionState.RESOLVED.value
                    ),
                )
                async for document in advertisement_cursor
            ]
        )
        normal_cursor = self._schedules.find(
            {
                "destination_id": destination_id,
                "action": "scheduled",
                "status": {"$in": list(_VISIBLE_NORMAL_STATES)},
            },
            {"due_at": 1, "version": 1, "status": 1},
        ).sort([("due_at", 1), ("_id", 1)])
        normal_publications = tuple(
            [
                CollisionNormalPublication(
                    job_id=document["_id"],
                    due_at=_aware(document["due_at"]),
                    version=document.get("version", 0),
                    movable=document["status"] in _MOVABLE_NORMAL_STATES,
                )
                async for document in normal_cursor
            ]
        )
        return PublicationCollisionSnapshot(advertisements, normal_publications)

    async def apply_plan(
        self,
        destination_id: int,
        plan: PublicationCollisionPlan,
        *,
        occurred_at: datetime,
    ) -> CollisionApplyOutcome:
        """Apply normal moves first, then make advertisement slots claimable."""
        changed = False
        for normal_move in plan.normal_moves:
            result = await self._schedules.update_one(
                {
                    "_id": normal_move.job_id,
                    "destination_id": destination_id,
                    "version": normal_move.expected_version,
                    "status": {"$in": list(_MOVABLE_NORMAL_STATES)},
                    "due_at": normal_move.old_due_at,
                },
                {
                    "$set": {"due_at": normal_move.new_due_at},
                    "$push": {
                        "due_time_history": {
                            "old_due_at": normal_move.old_due_at,
                            "new_due_at": normal_move.new_due_at,
                            "policy_version": 2,
                            "actor_id": 0,
                            "occurred_at": occurred_at,
                            "correlation_id": "advertisement-collision",
                            "reason": "advertisement_priority_minimum_gap",
                        }
                    },
                    "$inc": {"version": 1},
                },
            )
            if result.modified_count == 1:
                changed = True
                continue
            current = await self._schedules.find_one({"_id": normal_move.job_id})
            if current is None or _aware(current["due_at"]) != normal_move.new_due_at:
                return CollisionApplyOutcome.CONFLICT

        for advertisement_move in plan.advertisement_moves:
            audit = {
                "old_due_at": advertisement_move.old_due_at,
                "new_due_at": advertisement_move.new_due_at,
                "policy_version": 1,
                "reason": "advertisement_priority_minimum_gap",
                "occurred_at": occurred_at,
            }
            result = await self._advertisements.update_one(
                {
                    "_id": advertisement_move.slot_id,
                    "destination_id": destination_id,
                    "version": advertisement_move.expected_version,
                    "status": {"$in": list(_MOVABLE_ADVERTISEMENT_STATES)},
                },
                {
                    "$set": {
                        "effective_due_at": advertisement_move.new_due_at,
                        "collision_state": CollisionResolutionState.RESOLVED.value,
                        "immutable_collision_ids": list(plan.immutable_conflict_ids),
                        "updated_at": occurred_at,
                    },
                    "$push": {"collision_history": audit},
                    "$inc": {"version": 1},
                },
            )
            if result.modified_count == 1:
                changed = True
                continue
            current = await self._advertisements.find_one(
                {"_id": advertisement_move.slot_id}
            )
            if (
                current is None
                or current.get("collision_state")
                != CollisionResolutionState.RESOLVED.value
                or _aware(current.get("effective_due_at") or current["due_at"])
                != advertisement_move.new_due_at
            ):
                return CollisionApplyOutcome.CONFLICT

        await self._sync_queue(destination_id)
        return (
            CollisionApplyOutcome.APPLIED
            if changed
            else CollisionApplyOutcome.IDEMPOTENT
        )

    async def _sync_queue(self, destination_id: int) -> None:
        """Keep the existing reservation queue's embedded due metadata current."""
        cursor = self._schedules.find(
            {
                "destination_id": destination_id,
                "action": "scheduled",
                "status": {"$in": list(_MOVABLE_NORMAL_STATES)},
            },
            {"due_at": 1},
        ).sort([("due_at", 1), ("_id", 1)])
        items = [document async for document in cursor]
        await self._queues.update_one(
            {"_id": destination_id},
            {
                "$set": {
                    "slots": [
                        {"job_id": item["_id"], "due_at": item["due_at"]}
                        for item in items
                    ],
                    "last_due_at": items[-1]["due_at"] if items else None,
                }
            },
            upsert=True,
        )


__all__ = ("MongoPublicationCollisionRepository",)
