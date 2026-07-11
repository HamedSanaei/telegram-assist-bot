"""Immutable aggregate and value objects for original Telegram posts."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final, Self, cast

from telegram_assist_bot.domain.posts.entities import TelegramEntity
from telegram_assist_bot.domain.posts.errors import (
    InvalidPostIdentifierError,
    InvalidPostTransitionError,
    InvalidPostVersionError,
    InvalidSourceMessageIdentityError,
    OriginalContentMutationError,
    PostInvariantError,
    PostVersionConflictError,
)
from telegram_assist_bot.domain.posts.status import (
    PostStatus,
    StatusTransition,
    TransitionActorCategory,
    _canonical_utc,
    is_post_status_transition_allowed,
)

POST_RETENTION_PERIOD: Final[timedelta] = timedelta(days=14)
"""The fixed Milestone 0 retention period for a received post."""

_MAX_POST_ID_LENGTH: Final[int] = 128
_MAX_SOURCE_USERNAME_LENGTH: Final[int] = 128
_MAX_SOURCE_DISPLAY_NAME_LENGTH: Final[int] = 256


def _is_bounded_non_blank_string(value: object, maximum_length: int) -> bool:
    """Return whether text is non-blank without trimming or normalizing it."""
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= maximum_length
    )


def _freeze_entities(value: object) -> tuple[TelegramEntity, ...]:
    """Defensively copy and validate an original entity sequence."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PostInvariantError
    entities: tuple[object, ...] = tuple(value)
    if any(type(entity) is not TelegramEntity for entity in entities):
        raise PostInvariantError
    return cast("tuple[TelegramEntity, ...]", entities)


def _freeze_history(value: object) -> tuple[StatusTransition, ...]:
    """Defensively copy and validate a lifecycle history sequence."""
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise PostInvariantError
    history: tuple[object, ...] = tuple(value)
    if any(type(item) is not StatusTransition for item in history):
        raise PostInvariantError
    return cast("tuple[StatusTransition, ...]", history)


@dataclass(frozen=True, slots=True, order=True)
class PostId:
    """Represent an opaque internal post identifier without database semantics."""

    value: str

    def __post_init__(self) -> None:
        """Reject blank or oversized identifiers without retaining bad input."""
        if not _is_bounded_non_blank_string(self.value, _MAX_POST_ID_LENGTH):
            raise InvalidPostIdentifierError

    def __str__(self) -> str:
        """Return the unchanged opaque identifier value."""
        return self.value


@dataclass(frozen=True, slots=True, order=True)
class SourceMessageIdentity:
    """Identify one Telegram source message for idempotent ingestion."""

    source_channel_id: int
    source_message_id: int

    def __post_init__(self) -> None:
        """Require a non-zero signed channel ID and positive message ID."""
        if (
            type(self.source_channel_id) is not int
            or self.source_channel_id == 0
            or type(self.source_message_id) is not int
            or self.source_message_id <= 0
        ):
            raise InvalidSourceMessageIdentityError

    @property
    def as_tuple(self) -> tuple[int, int]:
        """Return the stable two-part idempotency key."""
        return (self.source_channel_id, self.source_message_id)


@dataclass(frozen=True, slots=True)
class OriginalPostContent:
    """Preserve original text, caption, and entity sequences exactly."""

    text: str | None = field(repr=False)
    caption: str | None = field(repr=False)
    text_entities: tuple[TelegramEntity, ...] = ()
    caption_entities: tuple[TelegramEntity, ...] = ()

    def __post_init__(self) -> None:
        """Freeze entity inputs without changing any Telegram source content."""
        if self.text is not None and type(self.text) is not str:
            raise PostInvariantError
        if self.caption is not None and type(self.caption) is not str:
            raise PostInvariantError
        text_entities = _freeze_entities(self.text_entities)
        caption_entities = _freeze_entities(self.caption_entities)
        if self.text is None and text_entities:
            raise PostInvariantError
        if self.caption is None and caption_entities:
            raise PostInvariantError
        object.__setattr__(self, "text_entities", text_entities)
        object.__setattr__(self, "caption_entities", caption_entities)


@dataclass(frozen=True, slots=True, eq=False)
class Post:
    """Represent one immutable post aggregate and its lifecycle snapshot.

    Equality and hashing use only ``post_id`` so successive lifecycle snapshots
    keep stable aggregate identity. Ingestion idempotency is deliberately exposed
    separately through ``source_identity``.
    """

    post_id: PostId
    source_identity: SourceMessageIdentity
    source_channel_username: str | None
    source_channel_display_name: str
    original_content: OriginalPostContent = field(repr=False)
    source_published_at: datetime
    received_at: datetime
    status: PostStatus = PostStatus.DISCOVERED
    version: int = 0
    transition_history: tuple[StatusTransition, ...] = field(
        default=(),
        repr=False,
    )
    expires_at: datetime = field(init=False)

    def __post_init__(self) -> None:
        """Canonicalize time and enforce all aggregate rehydration invariants."""
        if type(self.post_id) is not PostId:
            raise InvalidPostIdentifierError
        if type(self.source_identity) is not SourceMessageIdentity:
            raise InvalidSourceMessageIdentityError
        if type(self.original_content) is not OriginalPostContent:
            raise PostInvariantError
        if self.source_channel_username is not None and not (
            _is_bounded_non_blank_string(
                self.source_channel_username,
                _MAX_SOURCE_USERNAME_LENGTH,
            )
        ):
            raise PostInvariantError
        if not _is_bounded_non_blank_string(
            self.source_channel_display_name,
            _MAX_SOURCE_DISPLAY_NAME_LENGTH,
        ):
            raise PostInvariantError
        if not isinstance(self.status, PostStatus):
            raise PostInvariantError
        if type(self.version) is not int or self.version < 0:
            raise InvalidPostVersionError

        source_published_at = _canonical_utc(self.source_published_at)
        received_at = _canonical_utc(self.received_at)
        history = _freeze_history(self.transition_history)
        object.__setattr__(self, "source_published_at", source_published_at)
        object.__setattr__(self, "received_at", received_at)
        object.__setattr__(self, "transition_history", history)
        try:
            expires_at = received_at + POST_RETENTION_PERIOD
        except OverflowError:
            raise PostInvariantError from None
        object.__setattr__(self, "expires_at", expires_at)
        self._validate_lifecycle_history()

    def __eq__(self, other: object) -> bool:
        """Compare stable aggregate identity rather than mutable lifecycle state."""
        return isinstance(other, Post) and self.post_id == other.post_id

    def __hash__(self) -> int:
        """Hash the stable internal aggregate identifier."""
        return hash(self.post_id)

    @property
    def original_text(self) -> str | None:
        """Return the exact original Telegram message text."""
        return self.original_content.text

    @property
    def original_caption(self) -> str | None:
        """Return the exact original Telegram media caption."""
        return self.original_content.caption

    @property
    def original_text_entities(self) -> tuple[TelegramEntity, ...]:
        """Return entities associated with original message text."""
        return self.original_content.text_entities

    @property
    def original_caption_entities(self) -> tuple[TelegramEntity, ...]:
        """Return entities associated with the original media caption."""
        return self.original_content.caption_entities

    @property
    def idempotency_identity(self) -> SourceMessageIdentity:
        """Return the source identity used to deduplicate ingestion."""
        return self.source_identity

    def is_expired_at(self, occurred_at: datetime) -> bool:
        """Return whether the fixed retention boundary has been reached."""
        return _canonical_utc(occurred_at) >= self.expires_at

    def assert_original_content_matches(
        self,
        candidate: OriginalPostContent,
    ) -> None:
        """Reject a conflicting source-content version during idempotent ingest."""
        if type(candidate) is not OriginalPostContent:
            raise OriginalContentMutationError
        if candidate != self.original_content:
            raise OriginalContentMutationError

    def transition_to(
        self,
        new_status: PostStatus,
        *,
        expected_version: int,
        occurred_at: datetime,
        actor_category: TransitionActorCategory,
        reason: str,
        correlation_id: str | None = None,
    ) -> Self:
        """Return a new snapshot after one validated optimistic transition."""
        if type(expected_version) is not int or expected_version < 0:
            raise InvalidPostVersionError
        if expected_version != self.version:
            raise PostVersionConflictError(expected_version, self.version)
        if not is_post_status_transition_allowed(self.status, new_status):
            raise InvalidPostTransitionError

        normalized_time = _canonical_utc(occurred_at)
        previous_time = (
            self.transition_history[-1].occurred_at
            if self.transition_history
            else self.received_at
        )
        if normalized_time < previous_time:
            raise InvalidPostTransitionError
        self._validate_transition_timing(new_status, normalized_time)
        transition = StatusTransition(
            previous_status=self.status,
            new_status=new_status,
            occurred_at=normalized_time,
            actor_category=actor_category,
            reason=reason,
            correlation_id=correlation_id,
        )
        return replace(
            self,
            status=new_status,
            version=self.version + 1,
            transition_history=(*self.transition_history, transition),
        )

    def _validate_transition_timing(
        self,
        new_status: PostStatus,
        occurred_at: datetime,
    ) -> None:
        """Enforce receipt ordering and the exact expiration boundary."""
        if new_status is PostStatus.EXPIRED:
            if occurred_at < self.expires_at:
                raise InvalidPostTransitionError
        elif occurred_at >= self.expires_at:
            raise InvalidPostTransitionError

    def _validate_lifecycle_history(self) -> None:
        """Reject malformed rehydrated lifecycle chains and versions."""
        if self.version != len(self.transition_history):
            raise InvalidPostVersionError
        if not self.transition_history:
            if self.status is not PostStatus.DISCOVERED:
                raise PostInvariantError
            return

        expected_previous = PostStatus.DISCOVERED
        previous_time = self.received_at
        for transition in self.transition_history:
            if transition.previous_status is not expected_previous:
                raise PostInvariantError
            if transition.occurred_at < previous_time:
                raise PostInvariantError
            try:
                self._validate_transition_timing(
                    transition.new_status,
                    transition.occurred_at,
                )
            except InvalidPostTransitionError:
                raise PostInvariantError from None
            expected_previous = transition.new_status
            previous_time = transition.occurred_at
        if self.status is not expected_previous:
            raise PostInvariantError


__all__ = [
    "POST_RETENTION_PERIOD",
    "OriginalPostContent",
    "Post",
    "PostId",
    "SourceMessageIdentity",
]
