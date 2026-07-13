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


@dataclass(frozen=True, slots=True)
class ApprovalSyncClaim:
    """Identify one leased durable UI synchronization request."""

    post_id: str
    version: int
    owner: str


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
        self, post_id: str, *, owner: str, category: str, next_attempt_at: datetime
    ) -> bool:
        """Release an owned delivery for bounded retry."""
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
    ) -> None:
        """Persist a monotonic safe status used by every approval message."""
        ...

    async def destination_statuses(self, post_id: str) -> dict[int, str]:
        """Load safe per-destination status labels."""
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
    "ApprovalDeliveryClaim",
    "ApprovalPost",
    "ApprovalPostLoader",
    "ApprovalSyncClaim",
    "OperationalApprovalRepository",
)
