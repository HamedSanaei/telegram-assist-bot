"""Application-owned contracts for expired approval-message cleanup."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class ApprovalCleanupClaim:
    """Describe one leased Bot-owned approval reference."""

    reference_id: str
    post_id: str
    actor_id: int
    chat_id: int
    message_ids: tuple[int, ...]
    deleted_message_ids: frozenset[int]
    expires_at: datetime


class ApprovalDeleteOutcome(StrEnum):
    """Represent safe idempotent Bot deletion outcomes."""

    DELETED = "deleted"
    NOT_FOUND = "not_found"


class ApprovalDeleteError(RuntimeError):
    """Base safe approval-message deletion error."""

    error_category = "permanent"


class ApprovalDeleteTransientError(ApprovalDeleteError):
    """Report a retryable Bot transport or server failure."""

    error_category = "transient"


class ApprovalDeleteRateLimitError(ApprovalDeleteTransientError):
    """Report a bounded Bot flood-wait delay."""

    error_category = "rate_limit"

    def __init__(self, retry_after_seconds: int) -> None:
        """Retain only a non-negative provider delay."""
        self.retry_after_seconds = max(0, retry_after_seconds)
        super().__init__("Approval deletion is rate limited.")


class ApprovalDeleteUnavailableError(ApprovalDeleteError):
    """Report a permanently unavailable administrator chat or peer."""

    error_category = "unavailable"


class ApprovalMessageDeleteGateway(Protocol):
    """Delete only explicitly persisted Bot-owned approval message IDs."""

    async def delete_approval_message(
        self, chat_id: int, message_id: int
    ) -> ApprovalDeleteOutcome:
        """Delete one approval message idempotently."""
        ...


class ApprovalCleanupRepository(Protocol):
    """Persist bounded cleanup claims, progress, and terminal outcomes."""

    async def backfill_legacy_expirations(
        self, *, now: datetime, retention_days: int, limit: int
    ) -> int:
        """Backfill a bounded set of legacy references deterministically."""
        ...

    async def claim_expired(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> ApprovalCleanupClaim | None:
        """Claim one due reference or expired lease atomically."""
        ...

    async def expire_ui(self, claim: ApprovalCleanupClaim, *, owner: str) -> bool:
        """Disable callbacks and UI state before external deletion."""
        ...

    async def recheck_claim(
        self, reference_id: str, *, owner: str, now: datetime
    ) -> ApprovalCleanupClaim | None:
        """Reload a still-owned due claim immediately before deletion."""
        ...

    async def record_deleted_message(
        self, reference_id: str, message_id: int, *, owner: str
    ) -> bool:
        """Persist one idempotent deletion outcome."""
        ...

    async def complete_cleanup(
        self, reference_id: str, *, owner: str, outcome: str
    ) -> bool:
        """Finish one reference with deleted or unavailable state."""
        ...

    async def defer_cleanup(
        self,
        reference_id: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        category: str,
    ) -> bool:
        """Release one claim for bounded retry."""
        ...


__all__ = (
    "ApprovalCleanupClaim",
    "ApprovalCleanupRepository",
    "ApprovalDeleteError",
    "ApprovalDeleteOutcome",
    "ApprovalDeleteRateLimitError",
    "ApprovalDeleteTransientError",
    "ApprovalDeleteUnavailableError",
    "ApprovalMessageDeleteGateway",
)
