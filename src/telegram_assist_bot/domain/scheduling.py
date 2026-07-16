"""Pure durable scheduling and cancellation models."""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class ScheduleStatus(StrEnum):
    """Stable persistent schedule states."""

    PENDING = "Pending"
    CLAIMED = "Claimed"
    RUNNING = "Running"
    WAITING_FOR_RETRY = "WaitingForRetry"
    COMPLETED = "Completed"
    PERMANENT_FAILED = "PermanentFailed"
    OUTCOME_UNKNOWN = "OutcomeUnknown"
    CANCELLED = "Cancelled"


class CancellationPolicy(StrEnum):
    """Approved queue behavior after successful cancellation."""

    PRESERVE = "preserve"
    RECOMPACT = "recompact"


class CancellationResult(StrEnum):
    """Typed outcomes of CancelScheduledPost."""

    CANCELLED = "Cancelled"
    ALREADY_CANCELLED = "AlreadyCancelled"
    ALREADY_COMPLETED = "AlreadyCompleted"
    ALREADY_EXECUTING = "AlreadyExecuting"
    CONFLICT = "Conflict"
    PERMISSION_DENIED = "PermissionDenied"
    INVALID_STATE = "InvalidState"
    NOT_FOUND = "NotFound"


def schedule_identity(post_id: str, destination_id: int) -> str:
    """Create the deterministic version-one scheduled action identity."""
    payload = f"schedule:v1:{post_id}:{destination_id}:scheduled"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def validate_interval(seconds: float, *, maximum_seconds: int = 86_400) -> timedelta:
    """Validate an explicit finite bounded seconds interval."""
    if not math.isfinite(seconds) or seconds <= 0 or seconds > maximum_seconds:
        raise ValueError("Publication interval is outside the supported bounds.")
    return timedelta(seconds=seconds)


@dataclass(frozen=True, slots=True)
class DueTimeAudit:
    """Audit one trusted recompaction due-time change."""

    old_due_at: datetime
    new_due_at: datetime
    policy_version: int
    actor_id: int
    occurred_at: datetime
    correlation_id: str


@dataclass(frozen=True, slots=True)
class ScheduledPublication:
    """Represent one persistent versioned Job in a Destination queue."""

    job_id: str
    post_id: str
    destination_id: int
    due_at: datetime
    status: ScheduleStatus = ScheduleStatus.PENDING
    version: int = 0
    claim_owner: str | None = None
    lease_until: datetime | None = None
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    publication_id: str | None = None
    completed_at: datetime | None = None
    last_error_category: str | None = None
    due_time_history: tuple[DueTimeAudit, ...] = ()
    action: str = "scheduled"
    last_failure_type: str | None = None
    last_failure_reason_code: str | None = None

    def __post_init__(self) -> None:
        """Require and canonicalize an aware UTC due time."""
        if self.due_at.tzinfo is None:
            raise ValueError("Schedule due time must be timezone-aware.")
        object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))
        if self.action not in {"immediate", "scheduled"}:
            raise ValueError("Scheduled publication action is invalid.")


__all__ = (
    "CancellationPolicy",
    "CancellationResult",
    "DueTimeAudit",
    "ScheduleStatus",
    "ScheduledPublication",
    "schedule_identity",
    "validate_interval",
)
