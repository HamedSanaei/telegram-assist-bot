"""MongoDB outbox and destination leases for Telegram native schedules."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ports import (
    NativeScheduleCommand,
    NativeScheduleReceipt,
    NativeScheduleStatus,
)

if TYPE_CHECKING:
    from datetime import datetime

    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


def native_schedule_identity(
    post_id: str, destination_id: int, selection_version: int
) -> str:
    """Return a compact deterministic identity for one selection version."""
    value = f"{post_id}:{destination_id}:{selection_version}".encode()
    return sha256(value).hexdigest()


def _command(document: Document) -> NativeScheduleCommand:
    return NativeScheduleCommand(
        str(document["_id"]),
        str(document["post_id"]),
        int(document["destination_id"]),
        int(document["selection_version"]),
        NativeScheduleStatus(document["status"]),
        int(document.get("attempt_count", 0)),
        document.get("due_at"),
        tuple(int(value) for value in document.get("telegram_message_ids", ())),
        bool(document.get("follow_up_immediate", False)),
        str(document.get("operation", "schedule")),
    )


async def initialize_native_schedule_indexes(
    commands: AsyncCollection[Document], leases: AsyncCollection[Document]
) -> None:
    """Create deterministic command and fair claim indexes."""
    await commands.create_index(
        [
            ("status", ASCENDING),
            ("next_attempt_at", ASCENDING),
            ("created_at", ASCENDING),
        ],
        name="ix_native_schedule_claim",
    )
    await commands.create_index(
        [
            ("post_id", ASCENDING),
            ("destination_id", ASCENDING),
            ("selection_version", DESCENDING),
        ],
        name="ix_native_schedule_selection",
    )
    await leases.create_index(
        [("lease_until", ASCENDING)], name="ix_native_destination_lease"
    )


class MongoNativeScheduleRepository:
    """Persist native scheduling without touching legacy scheduled jobs."""

    def __init__(
        self,
        commands: AsyncCollection[Document],
        leases: AsyncCollection[Document],
    ) -> None:
        """Store separate command and destination-lease collections."""
        self._commands = commands
        self._leases = leases

    async def reserve(
        self,
        *,
        post_id: str,
        destination_id: int,
        selection_version: int,
        now: datetime,
    ) -> NativeScheduleCommand:
        """Insert or return one deterministic selection command."""
        command_id = native_schedule_identity(
            post_id, destination_id, selection_version
        )
        document: Document = {
            "_id": command_id,
            "post_id": post_id,
            "destination_id": destination_id,
            "selection_version": selection_version,
            "status": NativeScheduleStatus.PENDING.value,
            "attempt_count": 0,
            "created_at": now,
            "updated_at": now,
            "next_attempt_at": now,
            "telegram_message_ids": [],
            "follow_up_immediate": False,
            "operation": "schedule",
        }
        try:
            await self._commands.insert_one(document)
        except DuplicateKeyError:
            existing = await self._commands.find_one({"_id": command_id})
            if existing is None:
                raise
            return _command(existing)
        return _command(document)

    async def request_cancel_latest(
        self,
        *,
        post_id: str,
        destination_id: int,
        now: datetime,
        follow_up_immediate: bool = False,
    ) -> NativeScheduleCommand | None:
        """Turn the latest actionable command into a cancellation request."""
        query = {"post_id": post_id, "destination_id": destination_id}
        document = await self._commands.find_one_and_update(
            {
                **query,
                "status": {
                    "$in": [
                        NativeScheduleStatus.PENDING.value,
                        NativeScheduleStatus.CLAIMED.value,
                        NativeScheduleStatus.SCHEDULED.value,
                        NativeScheduleStatus.CANCEL_REQUESTED.value,
                    ]
                },
            },
            {
                "$set": {
                    "status": NativeScheduleStatus.CANCEL_REQUESTED.value,
                    "next_attempt_at": now,
                    "updated_at": now,
                    "claim_owner": None,
                    "lease_until": None,
                    "follow_up_immediate": follow_up_immediate,
                    "operation": "cancel",
                }
            },
            sort=[("selection_version", DESCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            document = await self._commands.find_one_and_update(
                {**query, "status": NativeScheduleStatus.REQUEST_STARTED.value},
                {
                    "$set": {
                        "cancel_after_schedule": True,
                        "follow_up_immediate": follow_up_immediate,
                        "updated_at": now,
                    }
                },
                sort=[("selection_version", DESCENDING)],
                return_document=ReturnDocument.AFTER,
            )
        return None if document is None else _command(document)

    async def claim_next(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> NativeScheduleCommand | None:
        """Claim one retry-ready command and quarantine ambiguous expired work."""
        # Never resend a lease that expired after the Telegram request started.
        await self._commands.update_many(
            {
                "status": NativeScheduleStatus.REQUEST_STARTED.value,
                "lease_until": {"$lte": now},
            },
            {
                "$set": {
                    "status": NativeScheduleStatus.OUTCOME_UNKNOWN.value,
                    "claim_owner": None,
                    "lease_until": None,
                    "updated_at": now,
                }
            },
        )
        document = await self._commands.find_one_and_update(
            {
                "$or": [
                    {
                        "status": {
                            "$in": [
                                NativeScheduleStatus.PENDING.value,
                                NativeScheduleStatus.CANCEL_REQUESTED.value,
                            ]
                        },
                        "next_attempt_at": {"$lte": now},
                    },
                    {
                        "status": NativeScheduleStatus.CLAIMED.value,
                        "lease_until": {"$lte": now},
                    },
                ]
            },
            {
                "$set": {
                    "status": NativeScheduleStatus.CLAIMED.value,
                    "claim_owner": owner,
                    "lease_until": lease_until,
                    "updated_at": now,
                },
                "$inc": {"attempt_count": 1},
            },
            sort=[
                ("next_attempt_at", ASCENDING),
                ("created_at", ASCENDING),
                ("_id", ASCENDING),
            ],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else _command(document)

    async def acquire_destination(
        self,
        destination_id: int,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
    ) -> bool:
        """Acquire one per-destination lease without stealing a live owner."""
        try:
            document = await self._leases.find_one_and_update(
                {
                    "_id": destination_id,
                    "$or": [
                        {"lease_until": {"$lte": now}},
                        {"owner": owner},
                    ],
                },
                {"$set": {"owner": owner, "lease_until": lease_until}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return document is not None and document.get("owner") == owner

    async def release_destination(self, destination_id: int, *, owner: str) -> None:
        """Release an owned destination lease."""
        await self._leases.delete_one({"_id": destination_id, "owner": owner})

    async def mark_request_started(
        self, command_id: str, *, owner: str, due_at: datetime | None = None
    ) -> bool:
        """Record the irreversible Telegram request boundary."""
        result = await self._commands.update_one(
            {
                "_id": command_id,
                "status": NativeScheduleStatus.CLAIMED.value,
                "claim_owner": owner,
            },
            {
                "$set": {
                    "status": NativeScheduleStatus.REQUEST_STARTED.value,
                    "due_at": due_at,
                }
            },
        )
        return result.modified_count == 1

    async def complete_scheduled(
        self,
        command_id: str,
        *,
        owner: str,
        receipt: NativeScheduleReceipt,
        now: datetime,
    ) -> NativeScheduleCommand:
        """Persist a successful native schedule receipt."""
        document = await self._commands.find_one_and_update(
            {
                "_id": command_id,
                "claim_owner": owner,
                "status": NativeScheduleStatus.REQUEST_STARTED.value,
            },
            [
                {
                    "$set": {
                        "status": {
                            "$cond": [
                                "$cancel_after_schedule",
                                NativeScheduleStatus.CANCEL_REQUESTED.value,
                                NativeScheduleStatus.SCHEDULED.value,
                            ]
                        },
                        "operation": {
                            "$cond": ["$cancel_after_schedule", "cancel", "schedule"]
                        },
                        "due_at": receipt.due_at,
                        "telegram_message_ids": list(receipt.message_ids),
                        "reconcile_at": receipt.due_at - timedelta(minutes=4),
                        "next_attempt_at": {
                            "$cond": ["$cancel_after_schedule", now, "$next_attempt_at"]
                        },
                        "claim_owner": None,
                        "lease_until": None,
                    }
                }
            ],
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise RuntimeError("Native schedule completion lost ownership.")
        return _command(document)

    async def complete_cancelled(
        self, command_id: str, *, owner: str
    ) -> NativeScheduleCommand:
        """Persist successful native cancellation."""
        document = await self._commands.find_one_and_update(
            {"_id": command_id, "claim_owner": owner},
            {
                "$set": {
                    "status": NativeScheduleStatus.CANCELLED.value,
                    "claim_owner": None,
                    "lease_until": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise RuntimeError("Native cancellation lost ownership.")
        return _command(document)

    async def fail(
        self,
        command_id: str,
        *,
        owner: str,
        now: datetime,
        next_attempt_at: datetime | None,
        failure_type: str,
        outcome_unknown: bool,
    ) -> None:
        """Persist bounded retry, terminal failure, or ambiguous outcome."""
        terminal = outcome_unknown or next_attempt_at is None
        await self._commands.update_one(
            {"_id": command_id, "claim_owner": owner},
            {
                "$set": {
                    "status": (
                        NativeScheduleStatus.OUTCOME_UNKNOWN.value
                        if outcome_unknown
                        else NativeScheduleStatus.PERMANENT_FAILED.value
                        if terminal
                        else NativeScheduleStatus.PENDING.value
                    ),
                    "next_attempt_at": next_attempt_at,
                    "last_failure_type": failure_type,
                    "claim_owner": None,
                    "lease_until": None,
                    "updated_at": now,
                }
            },
        )

    async def claim_reconciliation(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> NativeScheduleCommand | None:
        """Lease one scheduled command whose conservative check is due."""
        document = await self._commands.find_one_and_update(
            {
                "status": NativeScheduleStatus.SCHEDULED.value,
                "$and": [
                    {
                        "$or": [
                            {"reconcile_at": {"$lte": now}},
                            {"reconcile_at": {"$exists": False}},
                        ]
                    },
                    {
                        "$or": [
                            {"reconciliation_lease_until": {"$lte": now}},
                            {"reconciliation_lease_until": {"$exists": False}},
                            {"reconciliation_lease_until": None},
                        ]
                    },
                ],
            },
            {
                "$set": {
                    "reconciliation_owner": owner,
                    "reconciliation_lease_until": lease_until,
                }
            },
            sort=[("reconcile_at", ASCENDING), ("created_at", ASCENDING)],
            return_document=ReturnDocument.AFTER,
        )
        return None if document is None else _command(document)

    async def complete_reconciliation(
        self,
        command_id: str,
        *,
        owner: str,
        status: NativeScheduleStatus,
        due_at: datetime | None,
        next_check_at: datetime | None,
    ) -> NativeScheduleCommand:
        """Persist presence, external cancellation, resolution, or ambiguity."""
        document = await self._commands.find_one_and_update(
            {"_id": command_id, "reconciliation_owner": owner},
            {
                "$set": {
                    "status": status.value,
                    "due_at": due_at,
                    "reconcile_at": next_check_at,
                    "reconciliation_owner": None,
                    "reconciliation_lease_until": None,
                }
            },
            return_document=ReturnDocument.AFTER,
        )
        if document is None:
            raise RuntimeError("Native reconciliation lost ownership.")
        return _command(document)


__all__ = (
    "MongoNativeScheduleRepository",
    "initialize_native_schedule_indexes",
    "native_schedule_identity",
)
