"""Explicit schema-versioned mapping between post aggregates and MongoDB documents."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, overload

from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostDomainError,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    StatusTransition,
    TelegramEntity,
    TransitionActorCategory,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.errors import (
    InvalidPostDocumentError,
)

POST_DOCUMENT_SCHEMA_VERSION: Final[int] = 1
"""The only post document schema version understood by this mapper."""

_ROOT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "_id",
        "schema_version",
        "source_channel_id",
        "source_message_id",
        "source_channel_username",
        "source_channel_display_name",
        "original_content",
        "source_published_at",
        "source_published_at_microsecond_remainder",
        "received_at",
        "received_at_microsecond_remainder",
        "expires_at",
        "expires_at_microsecond_remainder",
        "status",
        "version",
        "transition_history",
    }
)
_CONTENT_FIELDS: Final[frozenset[str]] = frozenset(
    {"text", "caption", "text_entities", "caption_entities"}
)
_ENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {"offset_utf16", "length_utf16", "entity_type", "custom_emoji_id"}
)
_TRANSITION_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "previous_status",
        "new_status",
        "occurred_at",
        "occurred_at_microsecond_remainder",
        "actor_category",
        "reason",
        "correlation_id",
    }
)


def _require_exact_fields(
    document: Mapping[str, object], fields: frozenset[str]
) -> None:
    """Require a mapping to contain exactly one schema-defined field set."""
    actual_fields = frozenset(document)
    if not fields.issubset(actual_fields):
        raise InvalidPostDocumentError("missing_field")
    if actual_fields != fields:
        raise InvalidPostDocumentError


def _require_mapping(value: object, *, rule: str) -> Mapping[str, object]:
    """Return a document mapping or raise a content-safe mapper error."""
    if not isinstance(value, Mapping):
        raise InvalidPostDocumentError(rule)
    if any(type(key) is not str for key in value):
        raise InvalidPostDocumentError(rule)
    return value


@overload
def _require_string(value: object, *, optional: Literal[False] = False) -> str: ...


@overload
def _require_string(value: object, *, optional: Literal[True]) -> str | None: ...


def _require_string(value: object, *, optional: bool = False) -> str | None:
    """Return an exact string scalar with optional ``None`` support."""
    if optional and value is None:
        return None
    if type(value) is not str:
        raise InvalidPostDocumentError
    return value


def _require_integer(value: object) -> int:
    """Convert BSON integer subclasses while rejecting bool and coercion."""
    if not isinstance(value, int) or isinstance(value, bool):
        raise InvalidPostDocumentError
    return int(value)


def _canonical_document_datetime(value: object) -> datetime:
    """Validate an aware document datetime and canonicalize its instant to UTC."""
    if type(value) is not datetime:
        raise InvalidPostDocumentError("invalid_timestamp")
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidPostDocumentError("invalid_timestamp")
        canonical = value.astimezone(UTC)
    except (OverflowError, TypeError, ValueError):
        raise InvalidPostDocumentError("invalid_timestamp") from None
    if canonical.microsecond % 1000 != 0:
        raise InvalidPostDocumentError("invalid_timestamp")
    return canonical


def _require_microsecond_remainder(value: object) -> int:
    """Return a BSON precision remainder in the inclusive range 0..999."""
    remainder = _require_integer(value)
    if not 0 <= remainder <= 999:
        raise InvalidPostDocumentError("invalid_timestamp")
    return remainder


def _floor_to_millisecond(value: datetime) -> tuple[datetime, int]:
    """Split a UTC datetime into BSON milliseconds and its lost remainder."""
    remainder = value.microsecond % 1000
    return value.replace(microsecond=value.microsecond - remainder), remainder


def _ceil_to_millisecond(value: datetime) -> tuple[datetime, int]:
    """Ceil an expiry to BSON milliseconds so TTL cannot delete it early."""
    floor, remainder = _floor_to_millisecond(value)
    if remainder == 0:
        return floor, remainder
    try:
        return floor + timedelta(milliseconds=1), remainder
    except OverflowError:
        raise InvalidPostDocumentError("invalid_timestamp") from None


def _restore_floor_datetime(value: object, remainder: object) -> datetime:
    """Reconstruct a timestamp encoded by flooring to BSON milliseconds."""
    base = _canonical_document_datetime(value)
    lost_microseconds = _require_microsecond_remainder(remainder)
    try:
        return base + timedelta(microseconds=lost_microseconds)
    except OverflowError:
        raise InvalidPostDocumentError("invalid_timestamp") from None


def _restore_ceil_datetime(value: object, remainder: object) -> datetime:
    """Reconstruct an expiry encoded by ceiling to BSON milliseconds."""
    base = _canonical_document_datetime(value)
    lost_microseconds = _require_microsecond_remainder(remainder)
    if lost_microseconds == 0:
        return base
    try:
        return base - timedelta(microseconds=1000 - lost_microseconds)
    except OverflowError:
        raise InvalidPostDocumentError("invalid_timestamp") from None


def _entity_to_document(entity: TelegramEntity) -> dict[str, object]:
    """Serialize one SDK-independent Telegram entity without normalization."""
    return {
        "offset_utf16": entity.offset_utf16,
        "length_utf16": entity.length_utf16,
        "entity_type": entity.entity_type,
        "custom_emoji_id": entity.custom_emoji_id,
    }


def _entity_from_document(value: object) -> TelegramEntity:
    """Deserialize and domain-validate one Telegram entity document."""
    document = _require_mapping(value, rule="invalid_document")
    _require_exact_fields(document, _ENTITY_FIELDS)
    try:
        return TelegramEntity(
            offset_utf16=_require_integer(document["offset_utf16"]),
            length_utf16=_require_integer(document["length_utf16"]),
            entity_type=_require_string(document["entity_type"]),
            custom_emoji_id=_require_string(
                document["custom_emoji_id"],
                optional=True,
            ),
        )
    except PostDomainError:
        raise InvalidPostDocumentError from None


def _entities_from_document(value: object) -> tuple[TelegramEntity, ...]:
    """Deserialize an ordered BSON array of Telegram entities."""
    if type(value) is not list:
        raise InvalidPostDocumentError
    return tuple(_entity_from_document(item) for item in value)


def status_transition_to_document(transition: StatusTransition) -> dict[str, object]:
    """Serialize one transition for full documents or an atomic ``$push``."""
    if type(transition) is not StatusTransition:
        raise InvalidPostDocumentError
    occurred_at, remainder = _floor_to_millisecond(transition.occurred_at)
    return {
        "previous_status": transition.previous_status.value,
        "new_status": transition.new_status.value,
        "occurred_at": occurred_at,
        "occurred_at_microsecond_remainder": remainder,
        "actor_category": transition.actor_category.value,
        "reason": transition.reason,
        "correlation_id": transition.correlation_id,
    }


def _transition_from_document(value: object) -> StatusTransition:
    """Deserialize and domain-validate one lifecycle transition document."""
    document = _require_mapping(value, rule="invalid_document")
    _require_exact_fields(document, _TRANSITION_FIELDS)
    try:
        previous_status = PostStatus(_require_string(document["previous_status"]))
        new_status = PostStatus(_require_string(document["new_status"]))
        actor_category = TransitionActorCategory(
            _require_string(document["actor_category"])
        )
        return StatusTransition(
            previous_status=previous_status,
            new_status=new_status,
            occurred_at=_restore_floor_datetime(
                document["occurred_at"],
                document["occurred_at_microsecond_remainder"],
            ),
            actor_category=actor_category,
            reason=_require_string(document["reason"]),
            correlation_id=_require_string(
                document["correlation_id"],
                optional=True,
            ),
        )
    except (PostDomainError, ValueError):
        raise InvalidPostDocumentError from None


def _original_content_to_document(content: OriginalPostContent) -> dict[str, object]:
    """Serialize immutable original Telegram content exactly."""
    return {
        "text": content.text,
        "caption": content.caption,
        "text_entities": [
            _entity_to_document(entity) for entity in content.text_entities
        ],
        "caption_entities": [
            _entity_to_document(entity) for entity in content.caption_entities
        ],
    }


def _original_content_from_document(value: object) -> OriginalPostContent:
    """Deserialize exact source content without text or entity normalization."""
    document = _require_mapping(value, rule="invalid_document")
    _require_exact_fields(document, _CONTENT_FIELDS)
    try:
        return OriginalPostContent(
            text=_require_string(document["text"], optional=True),
            caption=_require_string(document["caption"], optional=True),
            text_entities=_entities_from_document(document["text_entities"]),
            caption_entities=_entities_from_document(document["caption_entities"]),
        )
    except PostDomainError:
        raise InvalidPostDocumentError from None


def post_to_document(post: Post) -> dict[str, object]:
    """Serialize a post aggregate into the exact version-1 document schema."""
    if type(post) is not Post:
        raise InvalidPostDocumentError
    source_published_at, source_published_remainder = _floor_to_millisecond(
        post.source_published_at
    )
    received_at, received_remainder = _floor_to_millisecond(post.received_at)
    expires_at, expires_remainder = _ceil_to_millisecond(post.expires_at)
    return {
        "_id": post.post_id.value,
        "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
        "source_channel_id": post.source_identity.source_channel_id,
        "source_message_id": post.source_identity.source_message_id,
        "source_channel_username": post.source_channel_username,
        "source_channel_display_name": post.source_channel_display_name,
        "original_content": _original_content_to_document(post.original_content),
        "source_published_at": source_published_at,
        "source_published_at_microsecond_remainder": source_published_remainder,
        "received_at": received_at,
        "received_at_microsecond_remainder": received_remainder,
        "expires_at": expires_at,
        "expires_at_microsecond_remainder": expires_remainder,
        "status": post.status.value,
        "version": post.version,
        "transition_history": [
            status_transition_to_document(transition)
            for transition in post.transition_history
        ],
    }


def post_from_document(value: object) -> Post:
    """Reconstruct and validate a post from the exact version-1 document schema."""
    document = _require_mapping(value, rule="invalid_document")
    _require_exact_fields(document, _ROOT_FIELDS)
    if (
        document["schema_version"] != POST_DOCUMENT_SCHEMA_VERSION
        or type(document["schema_version"]) is not int
    ):
        raise InvalidPostDocumentError("invalid_schema_version")
    history_value = document["transition_history"]
    if type(history_value) is not list:
        raise InvalidPostDocumentError
    expires_at = _restore_ceil_datetime(
        document["expires_at"],
        document["expires_at_microsecond_remainder"],
    )
    try:
        post = Post(
            post_id=PostId(_require_string(document["_id"])),
            source_identity=SourceMessageIdentity(
                source_channel_id=_require_integer(document["source_channel_id"]),
                source_message_id=_require_integer(document["source_message_id"]),
            ),
            source_channel_username=_require_string(
                document["source_channel_username"],
                optional=True,
            ),
            source_channel_display_name=_require_string(
                document["source_channel_display_name"]
            ),
            original_content=_original_content_from_document(
                document["original_content"]
            ),
            source_published_at=_restore_floor_datetime(
                document["source_published_at"],
                document["source_published_at_microsecond_remainder"],
            ),
            received_at=_restore_floor_datetime(
                document["received_at"],
                document["received_at_microsecond_remainder"],
            ),
            status=PostStatus(_require_string(document["status"])),
            version=_require_integer(document["version"]),
            transition_history=tuple(
                _transition_from_document(item) for item in history_value
            ),
        )
    except (PostDomainError, ValueError):
        raise InvalidPostDocumentError from None
    if post.expires_at != expires_at:
        raise InvalidPostDocumentError("invalid_expiration")
    return post


__all__ = [
    "POST_DOCUMENT_SCHEMA_VERSION",
    "InvalidPostDocumentError",
    "post_from_document",
    "post_to_document",
    "status_transition_to_document",
]
