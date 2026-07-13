"""Application-owned boundaries for durable operational approval work."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports.admin import ApprovalContent


@dataclass(frozen=True, slots=True)
class ApprovalDeliveryClaim:
    """Identify one leased logical approval delivery."""

    post_id: str
    owner: str
    lease_until: datetime
    ready_at: datetime | None = None
    attempt_count: int = 0
    administrator_states: tuple[ApprovalAdministratorDeliveryState, ...] = ()


@dataclass(frozen=True, slots=True)
class ApprovalAdministratorDeliveryState:
    """Carry safe retry progress for one administrator delivery."""

    administrator_id: int
    status: str
    attempt_count: int = 0
    next_attempt_at: datetime | None = None
    delivery_phase: str = "pending"
    failure_type: str | None = None


@dataclass(frozen=True, slots=True)
class ApprovalSyncClaim:
    """Identify one leased durable UI synchronization request."""

    post_id: str
    version: int
    owner: str


@dataclass(frozen=True, slots=True)
class DestinationPublicationState:
    """Carry safe durable publication UI state and timing metadata."""

    status: str
    action: str | None
    occurred_at: datetime
    due_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ApprovalPost:
    """Carry prepared approval content and safe source metadata."""

    post_id: str
    source_name: str
    source_username: str | None
    source_channel_id: int
    content: ApprovalContent
    category: str | None = None
    duplicate: str | None = None
    score: str | None = None
    source_message_id: int | None = None
    source_published_at: datetime | None = None
    content_type: str = "text"
    media_count: int = 0


class OperationalApprovalRepository(Protocol):
    """Persist delivery leases and canonical operational status."""

    async def claim_ready(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        ready_after: datetime | None = None,
    ) -> ApprovalDeliveryClaim | None:
        """Claim one ready, incomplete, or lease-expired delivery."""
        ...

    async def complete_delivery(self, post_id: str, *, owner: str) -> bool:
        """Complete an owned logical delivery."""
        ...

    async def release_delivery(
        self,
        post_id: str,
        *,
        owner: str,
        category: str,
        next_attempt_at: datetime,
        failure_type: str | None = None,
        delivery_phase: str | None = None,
        terminal: bool = False,
    ) -> bool:
        """Release an owned delivery for bounded retry."""
        ...

    async def record_administrator_delivery(
        self,
        post_id: str,
        administrator_id: int,
        *,
        owner: str,
        status: str,
        attempt_count: int,
        delivery_phase: str,
        next_attempt_at: datetime | None = None,
        failure_category: str | None = None,
        failure_type: str | None = None,
    ) -> bool:
        """Persist isolated safe progress for one administrator."""
        ...

    async def is_actionable(self, post_id: str) -> bool:
        """Return whether approval actions remain allowed for a ready Post."""
        ...

    async def record_destination_status(
        self,
        post_id: str,
        destination_id: int,
        *,
        status: str,
        version: int,
        at: datetime,
        action: str | None = None,
        due_at: datetime | None = None,
    ) -> None:
        """Persist a monotonic safe status used by every approval message."""
        ...

    async def destination_statuses(self, post_id: str) -> dict[int, str]:
        """Load safe per-destination status labels."""
        ...

    async def destination_states(
        self, post_id: str
    ) -> dict[int, DestinationPublicationState]:
        """Load safe per-destination state with durable timing metadata."""
        ...

    async def claim_sync(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> ApprovalSyncClaim | None:
        """Claim one pending or expired approval UI synchronization."""
        ...

    async def complete_sync(self, post_id: str, *, owner: str, version: int) -> bool:
        """Complete one synchronization unless a newer version arrived."""
        ...


class ApprovalPostLoader(Protocol):
    """Load prepared approval content without exposing MongoDB documents."""

    async def load(self, post_id: str) -> ApprovalPost:
        """Load one complete ready Post or fail safely."""
        ...


__all__ = (
    "ApprovalAdministratorDeliveryState",
    "ApprovalDeliveryClaim",
    "ApprovalPost",
    "ApprovalPostLoader",
    "ApprovalSyncClaim",
    "DestinationPublicationState",
    "OperationalApprovalRepository",
)
