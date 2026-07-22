"""Durable advertisement slot identities and scheduling state."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

from telegram_assist_bot.domain.publication_collision import CollisionResolutionState

_NONEXISTENT_LOCAL_TIME: Final[str] = "nonexistent_local_time"


class AdvertisementSlotStatus(StrEnum):
    """Minimal T050 lifecycle states for generated advertisement slots."""

    SCHEDULED = "scheduled"
    CLAIMED = "claimed"
    WAITING_FOR_RETRY = "waiting_for_retry"
    CANCELLED_BY_RECONCILIATION = "cancelled_by_reconciliation"
    COMPLETED = "completed"
    PERMANENT_FAILED = "permanent_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


def advertisement_slot_identity(
    campaign_id: str,
    destination_id: int,
    due_at: datetime,
) -> str:
    """Return a deterministic identity for campaign, destination, and UTC instant."""
    if not campaign_id or campaign_id.isspace():
        raise ValueError("campaign_id must be non-blank")
    if type(destination_id) is not int or destination_id == 0:
        raise ValueError("destination_id must be a non-zero integer")
    if due_at.tzinfo is None or due_at.utcoffset() is None:
        raise ValueError("due_at must be timezone-aware")
    canonical = due_at.astimezone(UTC).isoformat(timespec="seconds")
    raw = f"{campaign_id}:{destination_id}:{canonical}".encode("utf-8")  # noqa: UP012
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class AdvertisementSlot:
    """One durable scheduled advertisement execution boundary."""

    slot_id: str
    campaign_id: str
    destination_name: str
    destination_id: int
    due_at: datetime
    local_scheduled_at: datetime
    timezone_name: str
    source_snapshot_id: str
    source_snapshot_version: int
    config_fingerprint: str
    priority: int
    minimum_gap_seconds: int
    max_retries: int
    created_at: datetime
    updated_at: datetime
    status: AdvertisementSlotStatus = AdvertisementSlotStatus.SCHEDULED
    version: int = 0
    claim_owner: str | None = None
    lease_until: datetime | None = None
    claim_count: int = 0
    publication_attempt_count: int = 0
    next_attempt_at: datetime | None = None
    publication_id: str | None = None
    message_ids: tuple[int, ...] = ()
    published_at: datetime | None = None
    execution_delay_seconds: float | None = None
    last_error_category: str | None = None
    last_failure_type: str | None = None
    last_failure_reason_code: str | None = None
    effective_due_at: datetime | None = None
    collision_state: CollisionResolutionState = CollisionResolutionState.UNRESOLVED
    collision_history: tuple[AdvertisementCollisionAudit, ...] = ()
    immutable_collision_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Validate immutable identity, time, and persistence metadata."""
        for text_value, text_field in (
            (self.slot_id, "slot_id"),
            (self.campaign_id, "campaign_id"),
            (self.destination_name, "destination_name"),
            (self.timezone_name, "timezone_name"),
            (self.source_snapshot_id, "source_snapshot_id"),
            (self.config_fingerprint, "config_fingerprint"),
        ):
            if not text_value or text_value.isspace():
                raise ValueError(f"{text_field} must be non-blank")
        if type(self.destination_id) is not int or self.destination_id == 0:
            raise ValueError("destination_id must be a non-zero integer")
        for positive_value, positive_field in (
            (self.source_snapshot_version, "source_snapshot_version"),
            (self.minimum_gap_seconds, "minimum_gap_seconds"),
        ):
            if type(positive_value) is not int or positive_value <= 0:
                raise ValueError(f"{positive_field} must be a positive integer")
        for nonnegative_value, nonnegative_field in (
            (self.priority, "priority"),
            (self.max_retries, "max_retries"),
            (self.version, "version"),
            (self.claim_count, "claim_count"),
            (self.publication_attempt_count, "publication_attempt_count"),
        ):
            if type(nonnegative_value) is not int or nonnegative_value < 0:
                raise ValueError(f"{nonnegative_field} must be a non-negative integer")
        for timestamp in (
            self.due_at,
            self.local_scheduled_at,
            self.created_at,
            self.updated_at,
        ):
            if timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("slot timestamps must be timezone-aware")
        for optional_timestamp in (
            self.lease_until,
            self.next_attempt_at,
            self.published_at,
            self.effective_due_at,
        ):
            if optional_timestamp is not None and (
                optional_timestamp.tzinfo is None
                or optional_timestamp.utcoffset() is None
            ):
                raise ValueError("optional slot timestamps must be timezone-aware")
        if (
            self.execution_delay_seconds is not None
            and self.execution_delay_seconds < 0
        ):
            raise ValueError("execution_delay_seconds must be non-negative")
        expected = advertisement_slot_identity(
            self.campaign_id, self.destination_id, self.due_at
        )
        if self.slot_id != expected:
            raise ValueError("slot_id does not match slot identity")
        if not isinstance(self.status, AdvertisementSlotStatus):
            raise ValueError("status must be an AdvertisementSlotStatus")
        if not isinstance(self.collision_state, CollisionResolutionState):
            raise ValueError("collision_state must be a CollisionResolutionState")
        object.__setattr__(self, "due_at", self.due_at.astimezone(UTC))
        object.__setattr__(self, "created_at", self.created_at.astimezone(UTC))
        object.__setattr__(self, "updated_at", self.updated_at.astimezone(UTC))
        for field_name in (
            "lease_until",
            "next_attempt_at",
            "published_at",
            "effective_due_at",
        ):
            optional = getattr(self, field_name)
            if optional is not None:
                object.__setattr__(self, field_name, optional.astimezone(UTC))
        if self.effective_due_at is None:
            object.__setattr__(self, "effective_due_at", self.due_at)


@dataclass(frozen=True, slots=True)
class AdvertisementSlotAudit:
    """Sanitized record for a configured local time that could not form a slot."""

    campaign_id: str
    local_scheduled_value: str
    timezone_name: str
    reason: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        """Allow only the stable T050 DST skip reason."""
        if self.reason != _NONEXISTENT_LOCAL_TIME:
            raise ValueError("unsupported advertisement slot audit reason")
        if self.recorded_at.tzinfo is None or self.recorded_at.utcoffset() is None:
            raise ValueError("recorded_at must be timezone-aware")
        object.__setattr__(self, "recorded_at", self.recorded_at.astimezone(UTC))


@dataclass(frozen=True, slots=True)
class AdvertisementCollisionAudit:
    """Record one sanitized effective-time collision decision."""

    old_due_at: datetime
    new_due_at: datetime
    policy_version: int
    reason: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        """Require aware timestamps and the stable T052 reason."""
        if self.reason != "advertisement_priority_minimum_gap":
            raise ValueError("unsupported advertisement collision reason")
        if self.policy_version != 1:
            raise ValueError("unsupported advertisement collision policy version")
        for value in (self.old_due_at, self.new_due_at, self.occurred_at):
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError("collision audit timestamps must be aware")


__all__ = (
    "AdvertisementCollisionAudit",
    "AdvertisementSlot",
    "AdvertisementSlotAudit",
    "AdvertisementSlotStatus",
    "advertisement_slot_identity",
)
