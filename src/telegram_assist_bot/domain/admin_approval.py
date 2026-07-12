"""Pure administration, callback, approval, and destination-selection models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum


class AdminRole(StrEnum):
    """Supported administrative role."""

    ADMIN = "admin"


class AdminPermission(StrEnum):
    """Stable application-owned approval permissions."""

    VIEW = "approval.view"
    TOGGLE = "approval.toggle"


@dataclass(frozen=True, slots=True)
class Administrator:
    """Describe one explicitly configured administrator."""

    telegram_user_id: int
    active: bool
    role: str
    permissions: frozenset[AdminPermission]
    allowed_destination_ids: frozenset[int]


class SelectionMode(StrEnum):
    """Represent the mutually exclusive future publication selection."""

    NONE = "none"
    IMMEDIATE = "immediate"
    SCHEDULED = "scheduled"


@dataclass(frozen=True, slots=True)
class SelectionAudit:
    """Record one non-sensitive destination selection transition."""

    actor_id: int
    previous: SelectionMode
    current: SelectionMode
    occurred_at: datetime
    correlation_id: str


@dataclass(frozen=True, slots=True)
class DestinationSelection:
    """Keep one versioned selection for one Post and Destination."""

    post_id: str
    destination_id: int
    mode: SelectionMode = SelectionMode.NONE
    version: int = 0
    history: tuple[SelectionAudit, ...] = field(default_factory=tuple)

    def toggle(
        self,
        requested: SelectionMode,
        *,
        actor_id: int,
        occurred_at: datetime,
        correlation_id: str,
    ) -> DestinationSelection:
        """Apply the complete approved toggle table immutably."""
        if requested is SelectionMode.NONE:
            raise ValueError("Requested selection mode must be actionable.")
        if occurred_at.tzinfo is None:
            raise ValueError("Selection time must be timezone-aware.")
        current = SelectionMode.NONE if self.mode is requested else requested
        audit = SelectionAudit(
            actor_id,
            self.mode,
            current,
            occurred_at.astimezone(UTC),
            correlation_id,
        )
        return DestinationSelection(
            self.post_id,
            self.destination_id,
            current,
            self.version + 1,
            (*self.history, audit),
        )


class CallbackAction(StrEnum):
    """Actions supported by Milestone 3 callback tokens."""

    TOGGLE_IMMEDIATE = "toggle_immediate"
    TOGGLE_SCHEDULED = "toggle_scheduled"


@dataclass(frozen=True, slots=True, repr=False)
class CallbackClaims:
    """Keep opaque-token claims exclusively on the server."""

    token_digest: str
    actor_id: int
    action: CallbackAction
    post_id: str
    destination_id: int | None
    issued_at: datetime
    expires_at: datetime
    version: int = 1
    revoked: bool = False
    correlation_id: str | None = None


class ApprovalSyncState(StrEnum):
    """Represent synchronization state for one approval reference."""

    CURRENT = "current"
    RETRY = "retry"
    INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class ApprovalReference:
    """Reference one independently delivered private administrator UI."""

    reference_id: str
    actor_id: int
    chat_id: int
    post_id: str
    header_message_id: int
    content_message_ids: tuple[int, ...]
    rendered_version: int = 0
    active: bool = True
    sync_state: ApprovalSyncState = ApprovalSyncState.CURRENT
    attempt_count: int = 0
    next_retry_at: datetime | None = None
    last_error_category: str | None = None


__all__ = (
    "AdminPermission",
    "AdminRole",
    "Administrator",
    "ApprovalReference",
    "ApprovalSyncState",
    "CallbackAction",
    "CallbackClaims",
    "DestinationSelection",
    "SelectionAudit",
    "SelectionMode",
)
