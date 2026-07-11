"""Immutable status and transition records for the Milestone 0 post lifecycle."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from telegram_assist_bot.domain.posts.errors import (
    InvalidPostTransitionError,
    NaiveDatetimeError,
    PostInvariantError,
)

__all__ = [
    "ALLOWED_POST_STATUS_TRANSITIONS",
    "PostStatus",
    "StatusTransition",
    "TransitionActorCategory",
    "is_post_status_transition_allowed",
]


class PostStatus(StrEnum):
    """Represent only the post states implemented in Milestone 0."""

    DISCOVERED = "Discovered"
    STORED = "Stored"
    EXPIRED = "Expired"


class TransitionActorCategory(StrEnum):
    """Classify the trusted actor responsible for a status transition."""

    SERVICE = "service"
    ADMINISTRATOR = "administrator"


ALLOWED_POST_STATUS_TRANSITIONS: Final[
    MappingProxyType[PostStatus, frozenset[PostStatus]]
] = MappingProxyType(
    {
        PostStatus.DISCOVERED: frozenset(
            {
                PostStatus.STORED,
                PostStatus.EXPIRED,
            }
        ),
        PostStatus.STORED: frozenset({PostStatus.EXPIRED}),
        PostStatus.EXPIRED: frozenset(),
    }
)
"""The complete immutable transition table implemented for Milestone 0."""


def _canonical_utc(value: datetime) -> datetime:
    """Return an aware datetime in canonical UTC form.

    Raises:
        PostInvariantError: If ``value`` is not a datetime.
        NaiveDatetimeError: If ``value`` has no usable UTC offset.
    """
    if type(value) is not datetime:
        raise PostInvariantError
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise NaiveDatetimeError
        return value.astimezone(UTC)
    except (OverflowError, TypeError, ValueError):
        raise PostInvariantError from None


def is_post_status_transition_allowed(
    previous_status: object,
    new_status: object,
) -> bool:
    """Return whether the exact typed status edge is allowed."""
    if not isinstance(previous_status, PostStatus) or not isinstance(
        new_status, PostStatus
    ):
        return False
    return new_status in ALLOWED_POST_STATUS_TRANSITIONS[previous_status]


@dataclass(frozen=True, slots=True)
class StatusTransition:
    """Record one validated, serializable post lifecycle transition."""

    previous_status: PostStatus
    new_status: PostStatus
    occurred_at: datetime
    actor_category: TransitionActorCategory
    reason: str = field(repr=False)
    correlation_id: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """Validate the transition edge and canonicalize its timestamp to UTC."""
        if not is_post_status_transition_allowed(self.previous_status, self.new_status):
            raise InvalidPostTransitionError
        if type(self.actor_category) is not TransitionActorCategory:
            raise PostInvariantError
        reason: object = self.reason
        if type(reason) is not str or not reason.strip() or len(reason) > 1024:
            raise PostInvariantError
        correlation_id: object = self.correlation_id
        if correlation_id is not None and (
            type(correlation_id) is not str
            or not correlation_id.strip()
            or len(correlation_id) > 128
        ):
            raise PostInvariantError
        object.__setattr__(self, "occurred_at", _canonical_utc(self.occurred_at))
