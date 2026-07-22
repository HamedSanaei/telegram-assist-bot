"""Application-owned repository ports for versioned advertisement snapshots."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.advertisement_slot import (
        AdvertisementSlot,
        AdvertisementSlotAudit,
    )
    from telegram_assist_bot.domain.advertisement_source import (
        AdvertisementSourceSnapshot,
    )


class AdvertisementReportKind(StrEnum):
    """Supported bounded advertisement report projections."""

    TODAY = "today"
    UPCOMING = "upcoming"
    FAILURES = "failures"


@dataclass(frozen=True, slots=True)
class AdvertisementReportQuery:
    """Describe one authorized, bounded UTC repository query."""

    kind: AdvertisementReportKind
    starts_at: datetime
    ends_at: datetime
    allowed_destination_ids: frozenset[int]
    limit: int

    def __post_init__(self) -> None:
        """Reject unbounded, naive, empty-access, or reversed queries."""
        if (
            self.starts_at.tzinfo is None
            or self.starts_at.utcoffset() is None
            or self.ends_at.tzinfo is None
            or self.ends_at.utcoffset() is None
            or self.starts_at > self.ends_at
            or not self.allowed_destination_ids
            or type(self.limit) is not int
            or not 1 <= self.limit <= 51
        ):
            raise ValueError("advertisement report query is invalid")


@dataclass(frozen=True, slots=True)
class AdvertisementReportRecord:
    """Application-owned safe projection of one advertisement execution."""

    record_id: str
    campaign_id: str
    destination_name: str
    destination_id: int
    status: str
    scheduled_at: datetime
    published_at: datetime | None
    message_ids: tuple[int, ...]
    retry_count: int
    execution_delay_seconds: float | None
    failure_category: str | None
    failure_reason_code: str | None
    latest_failure_at: datetime | None


@runtime_checkable
class AdvertisementRepository(Protocol):
    """Repository port for persisting versioned advertisement source snapshots."""

    async def get_current_snapshot(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
    ) -> AdvertisementSourceSnapshot | None:
        """Return the current active snapshot for a campaign source identity."""
        ...

    async def get_snapshot_by_id(
        self, snapshot_id: str
    ) -> AdvertisementSourceSnapshot | None:
        """Return one exact immutable source snapshot by identity."""
        ...

    async def save_initial_snapshot(
        self,
        snapshot: AdvertisementSourceSnapshot,
    ) -> AdvertisementSourceSnapshot:
        """Persist the initial snapshot for a campaign source identity."""
        ...

    async def commit_changed_snapshot(
        self,
        new_snapshot: AdvertisementSourceSnapshot,
        expected_current_version: int,
        retention_days: int,
    ) -> AdvertisementSourceSnapshot:
        """CAS-replace current content and retain the immutable prior snapshot."""
        ...

    async def record_unchanged_check(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
        fetched_at: datetime,
    ) -> None:
        """Update last_successful_fetch_at timestamp for current snapshot atomically."""
        ...

    async def record_failed_check(
        self,
        campaign_id: str,
        source_identity_fingerprint: str,
        failed_at: datetime,
        error_reason: str,
    ) -> None:
        """Record sanitized failure metadata without overwriting source content."""
        ...

    async def initialize_indexes(self) -> None:
        """Initialize MongoDB indexes idempotently."""
        ...


@runtime_checkable
class AdvertisementSlotRepository(Protocol):
    """Repository port for idempotent slot expansion and reconciliation."""

    async def initialize_indexes(self) -> None:
        """Create unique identity and due-query indexes idempotently."""
        ...

    async def reconcile_campaign_slots(
        self,
        campaign_id: str,
        desired_slots: tuple[AdvertisementSlot, ...],
        audits: tuple[AdvertisementSlotAudit, ...],
        *,
        now: datetime,
    ) -> tuple[AdvertisementSlot, ...]:
        """Upsert desired future slots and cancel obsolete unexecuted slots."""
        ...

    async def list_campaign_slots(
        self, campaign_id: str
    ) -> tuple[AdvertisementSlot, ...]:
        """Return campaign slots in deterministic due/destination order."""
        ...

    async def claim_due_slot(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
    ) -> AdvertisementSlot | None:
        """Atomically claim the oldest due or expired-lease slot."""
        ...

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
        """Persist a successful owned execution exactly once."""
        ...

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
        """Release an owned slot into durable bounded retry waiting."""
        ...

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
        """Persist a terminal safe failure without fabricating success."""
        ...


@runtime_checkable
class AdvertisementReportRepository(Protocol):
    """Read-only bounded projection port for administrator reports."""

    async def list_report_records(
        self, query: AdvertisementReportQuery
    ) -> tuple[AdvertisementReportRecord, ...]:
        """Return at most the explicitly bounded authorized projection."""
        ...
