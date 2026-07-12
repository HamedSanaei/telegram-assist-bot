"""Pure publication identity, state, and result models."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


class PublicationState(StrEnum):
    """Stable states for one idempotent destination Publication."""

    PENDING = "Pending"
    CLAIMED = "Claimed"
    WAITING_FOR_RETRY = "WaitingForRetry"
    SUCCEEDED = "Succeeded"
    PERMANENT_FAILED = "PermanentFailed"
    OUTCOME_UNKNOWN = "OutcomeUnknown"


class PublicationFailureCategory(StrEnum):
    """Application-owned Publisher failure categories."""

    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    TRANSIENT = "transient"
    PERMISSION = "permission"
    PERMANENT = "permanent"
    AMBIGUOUS = "ambiguous"


def publication_identity(
    post_id: str, destination_id: int, action: str = "immediate"
) -> str:
    """Create a deterministic version-one action-scoped Publication identity."""
    if action not in {"immediate", "scheduled"}:
        raise ValueError("Publication action is invalid.")
    payload = f"publication:v1:{post_id}:{destination_id}:{action}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Publication:
    """Persist safe Publication state without Telegram payload or credentials."""

    publication_id: str
    post_id: str
    destination_id: int
    state: PublicationState = PublicationState.PENDING
    version: int = 0
    claim_owner: str | None = None
    lease_until: datetime | None = None
    attempt_count: int = 0
    attempted_at: datetime | None = None
    next_attempt_at: datetime | None = None
    message_ids: tuple[int, ...] = ()
    published_at: datetime | None = None
    error_category: str | None = None
    correlation_id: str | None = None

    def __post_init__(self) -> None:
        """Reject naive persistence timestamps."""
        for value in (
            self.lease_until,
            self.attempted_at,
            self.next_attempt_at,
            self.published_at,
        ):
            if value is not None and value.tzinfo is None:
                raise ValueError("Publication timestamps must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class PublishedMessage:
    """Return deterministic destination message identifiers and UTC time."""

    message_ids: tuple[int, ...]
    published_at: datetime

    def __post_init__(self) -> None:
        """Require identifiers and canonicalize publication time."""
        if not self.message_ids or self.published_at.tzinfo is None:
            raise ValueError("Published result must contain IDs and aware time.")
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))


__all__ = (
    "Publication",
    "PublicationFailureCategory",
    "PublicationState",
    "PublishedMessage",
    "publication_identity",
)
