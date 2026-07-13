"""Application-owned contracts for native Telegram scheduled messages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports.publication import PublicationPayload


class NativeScheduleStatus(StrEnum):
    """Describe one durable native scheduling command."""

    PENDING = "pending"
    CLAIMED = "claimed"
    REQUEST_STARTED = "request_started"
    SCHEDULED = "scheduled"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    CANCELLED_EXTERNAL = "cancelled_external"
    PERMANENT_FAILED = "permanent_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"
    RESOLVED = "resolved"


@dataclass(frozen=True, slots=True)
class NativeScheduleCommand:
    """Carry one SDK-neutral native scheduling command."""

    command_id: str
    post_id: str
    destination_id: int
    selection_version: int
    status: NativeScheduleStatus
    attempt_count: int = 0
    due_at: datetime | None = None
    telegram_message_ids: tuple[int, ...] = ()
    follow_up_immediate: bool = False
    operation: str = "schedule"


@dataclass(frozen=True, slots=True)
class NativeScheduledMessage:
    """Describe one message currently present in Telegram's schedule."""

    message_id: int
    due_at: datetime


@dataclass(frozen=True, slots=True)
class NativeScheduleReceipt:
    """Return Telegram scheduled identities and their exact UTC due time."""

    message_ids: tuple[int, ...]
    due_at: datetime


class NativeScheduleRepository(Protocol):
    """Persist native commands, claims, and per-destination leases."""

    async def reserve(
        self,
        *,
        post_id: str,
        destination_id: int,
        selection_version: int,
        now: datetime,
    ) -> NativeScheduleCommand:
        """Idempotently reserve one selection version."""
        ...

    async def request_cancel_latest(
        self,
        *,
        post_id: str,
        destination_id: int,
        now: datetime,
        follow_up_immediate: bool = False,
    ) -> NativeScheduleCommand | None:
        """Request cancellation of the latest actionable native schedule."""
        ...

    async def claim_next(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> NativeScheduleCommand | None:
        """Atomically claim one due command."""
        ...

    async def acquire_destination(
        self,
        destination_id: int,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
    ) -> bool:
        """Acquire exclusive scheduling access for one destination."""
        ...

    async def release_destination(self, destination_id: int, *, owner: str) -> None:
        """Release an owned destination lease."""
        ...

    async def mark_request_started(
        self, command_id: str, *, owner: str, due_at: datetime | None = None
    ) -> bool:
        """Persist the irreversible Telegram request boundary."""
        ...

    async def complete_scheduled(
        self,
        command_id: str,
        *,
        owner: str,
        receipt: NativeScheduleReceipt,
        now: datetime,
    ) -> NativeScheduleCommand:
        """Persist native message identities and exact due time."""
        ...

    async def complete_cancelled(
        self, command_id: str, *, owner: str
    ) -> NativeScheduleCommand:
        """Complete an owned native cancellation."""
        ...

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
        """Release, terminally fail, or quarantine ambiguous work."""
        ...

    async def claim_reconciliation(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> NativeScheduleCommand | None:
        """Claim one native schedule for periodic Telegram reconciliation."""
        ...

    async def complete_reconciliation(
        self,
        command_id: str,
        *,
        owner: str,
        status: NativeScheduleStatus,
        due_at: datetime | None,
        next_check_at: datetime | None,
    ) -> NativeScheduleCommand:
        """Persist one conservative reconciliation result."""
        ...


class TelegramNativeSchedulerGateway(Protocol):
    """Use the runtime-owned User API session for native scheduling."""

    async def list_scheduled(
        self, destination_id: int, *, timeout_seconds: float
    ) -> tuple[NativeScheduledMessage, ...]:
        """Read every native Scheduled Message from one destination."""
        ...

    async def schedule(
        self,
        payload: PublicationPayload,
        *,
        due_at: datetime,
        timeout_seconds: float,
    ) -> NativeScheduleReceipt:
        """Schedule one text, media item, or album natively."""
        ...

    async def cancel(
        self,
        destination_id: int,
        message_ids: tuple[int, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        """Delete explicitly identified native Scheduled Messages."""
        ...


__all__ = (
    "NativeScheduleCommand",
    "NativeScheduleReceipt",
    "NativeScheduleRepository",
    "NativeScheduleStatus",
    "NativeScheduledMessage",
    "TelegramNativeSchedulerGateway",
)
