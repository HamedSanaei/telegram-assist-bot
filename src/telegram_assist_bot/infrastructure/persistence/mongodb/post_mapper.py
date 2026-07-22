"""Explicit schema-versioned mapping between post aggregates and MongoDB documents."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final, Literal, overload

from telegram_assist_bot.domain.advertisement import (
    AdvertisementCheckFailure,
    AdvertisementCheckResult,
    AdvertisementDomainError,
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
)
from telegram_assist_bot.domain.categories import (
    CategorizationCheckFailure,
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
)
from telegram_assist_bot.domain.duplicates import (
    SemanticDuplicateFailure,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicateResult,
    SemanticDuplicateState,
)
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
from telegram_assist_bot.domain.scoring import (
    ScoringFailure,
    ScoringFailurePolicy,
    ScoringResult,
    ScoringState,
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
        "next_stage_claimed_at",
        "next_stage_claim_correlation_id",
        "advertisement_processing",
        "semantic_duplicate_processing",
        "categorization_processing",
        "scoring_processing",
    }
)
_ADVERTISEMENT_FIELDS: Final[frozenset[str]] = frozenset(
    {"state", "version", "job_id", "result", "failure"}
)
_ADVERTISEMENT_RESULT_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "is_advertisement",
        "confidence",
        "reason",
        "provider_name",
        "model_name",
        "checked_at",
        "checked_at_microsecond_remainder",
        "prompt_version",
        "schema_version",
        "attempt_number",
        "fallback_count",
        "cache_hit",
        "cache_age_seconds",
    }
)
_ADVERTISEMENT_FAILURE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "policy",
        "failure_category",
        "failure_type",
        "failed_at",
        "failed_at_microsecond_remainder",
        "attempted_candidates_count",
        "retry_count",
        "fallback_count",
        "next_retry_at",
        "next_retry_at_microsecond_remainder",
    }
)
_SEMANTIC_FIELDS = frozenset({"state", "version", "job_id", "result", "failure"})
_SEMANTIC_RESULT_FIELDS = frozenset(
    {
        "method",
        "is_duplicate",
        "similarity",
        "confidence",
        "matched_post_id",
        "reason",
        "provider_name",
        "model_name",
        "checked_at",
        "checked_at_microsecond_remainder",
        "prompt_version",
        "schema_version",
        "attempt_number",
        "fallback_count",
        "cache_hit",
        "cache_age_seconds",
    }
)
_SEMANTIC_FAILURE_FIELDS = frozenset(
    {
        "policy",
        "failure_category",
        "failed_at",
        "failed_at_microsecond_remainder",
        "next_retry_at",
        "next_retry_at_microsecond_remainder",
    }
)
_CATEGORIZATION_FIELDS = frozenset({"state", "version", "job_id", "result", "failure"})
_CATEGORIZATION_RESULT_FIELDS = frozenset(
    {
        "category_id",
        "method",
        "policy_version",
        "assigned_at",
        "assigned_at_microsecond_remainder",
        "rule_id",
        "reason",
        "confidence",
        "provider_name",
        "model_name",
        "prompt_version",
        "schema_version",
        "attempt_number",
        "fallback_count",
        "cache_hit",
        "cache_age",
    }
)
_CATEGORIZATION_FAILURE_FIELDS = frozenset(
    {
        "policy",
        "failure_category",
        "failed_at",
        "failed_at_microsecond_remainder",
        "attempted_candidates_count",
        "retry_count",
        "fallback_count",
        "next_retry_at",
        "next_retry_at_microsecond_remainder",
    }
)
_SCORING_FIELDS = frozenset(
    {
        "state",
        "version",
        "job_id",
        "due_at",
        "due_at_microsecond_remainder",
        "result",
        "failure",
    }
)
_SCORING_RESULT_FIELDS = frozenset(
    {
        "score",
        "confidence",
        "reason",
        "provider_name",
        "model_name",
        "scored_at",
        "scored_at_microsecond_remainder",
        "prompt_version",
        "schema_version",
        "attractiveness_probability",
        "engagement_probability",
        "headline_quality",
        "freshness",
        "news_value",
        "writing_quality",
        "cache_hit",
        "cache_age_seconds",
        "attempt_number",
        "fallback_count",
    }
)
_SCORING_FAILURE_FIELDS = frozenset(
    {
        "policy",
        "failure_category",
        "failed_at",
        "failed_at_microsecond_remainder",
        "next_retry_at",
        "next_retry_at_microsecond_remainder",
    }
)
_CONTENT_FIELDS: Final[frozenset[str]] = frozenset(
    {"text", "caption", "text_entities", "caption_entities"}
)
_ENTITY_FIELDS: Final[frozenset[str]] = frozenset(
    {"offset_utf16", "length_utf16", "entity_type", "custom_emoji_id", "url"}
)
_LEGACY_ENTITY_FIELDS: Final[frozenset[str]] = _ENTITY_FIELDS - {"url"}
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


@overload
def _require_boolean(value: object, *, optional: Literal[False] = False) -> bool: ...


@overload
def _require_boolean(value: object, *, optional: Literal[True]) -> bool | None: ...


def _require_boolean(value: object, *, optional: bool = False) -> bool | None:
    """Return an exact Boolean scalar with optional ``None`` support."""
    if optional and value is None:
        return None
    if type(value) is not bool:
        raise InvalidPostDocumentError
    return value


@overload
def _require_float(value: object, *, optional: Literal[False] = False) -> float: ...


@overload
def _require_float(value: object, *, optional: Literal[True]) -> float | None: ...


def _require_float(value: object, *, optional: bool = False) -> float | None:
    """Return an exact BSON double without accepting integers or coercion."""
    if optional and value is None:
        return None
    if type(value) is not float:
        raise InvalidPostDocumentError
    return value


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
        "url": entity.url,
    }


def _entity_from_document(value: object) -> TelegramEntity:
    """Deserialize and domain-validate one Telegram entity document."""
    document = _require_mapping(value, rule="invalid_document")
    if frozenset(document) not in {_ENTITY_FIELDS, _LEGACY_ENTITY_FIELDS}:
        raise InvalidPostDocumentError("invalid_document")
    try:
        return TelegramEntity(
            offset_utf16=_require_integer(document["offset_utf16"]),
            length_utf16=_require_integer(document["length_utf16"]),
            entity_type=_require_string(document["entity_type"]),
            custom_emoji_id=_require_string(
                document["custom_emoji_id"],
                optional=True,
            ),
            url=_require_string(document.get("url"), optional=True),
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


def advertisement_processing_to_document(post: Post) -> dict[str, object]:
    """Serialize the additive advertisement state for atomic repository updates."""
    result_document: dict[str, object] | None = None
    if post.advertisement_result is not None:
        result = post.advertisement_result
        checked_at, checked_remainder = _floor_to_millisecond(result.checked_at)
        result_document = {
            "is_advertisement": result.is_advertisement,
            "confidence": result.confidence,
            "reason": result.reason,
            "provider_name": result.provider_name,
            "model_name": result.model_name,
            "checked_at": checked_at,
            "checked_at_microsecond_remainder": checked_remainder,
            "prompt_version": result.prompt_version,
            "schema_version": result.schema_version,
            "attempt_number": result.attempt_number,
            "fallback_count": result.fallback_count,
            "cache_hit": result.cache_hit,
            "cache_age_seconds": result.cache_age_seconds,
        }
    failure_document: dict[str, object] | None = None
    if post.advertisement_failure is not None:
        failure = post.advertisement_failure
        failed_at, failed_remainder = _floor_to_millisecond(failure.failed_at)
        next_retry_at: datetime | None = None
        next_retry_remainder = 0
        if failure.next_retry_at is not None:
            next_retry_at, next_retry_remainder = _floor_to_millisecond(
                failure.next_retry_at
            )
        failure_document = {
            "policy": failure.policy.value,
            "failure_category": failure.failure_category,
            "failure_type": failure.failure_type,
            "failed_at": failed_at,
            "failed_at_microsecond_remainder": failed_remainder,
            "attempted_candidates_count": failure.attempted_candidates_count,
            "retry_count": failure.retry_count,
            "fallback_count": failure.fallback_count,
            "next_retry_at": next_retry_at,
            "next_retry_at_microsecond_remainder": next_retry_remainder,
        }
    return {
        "state": post.advertisement_state.value,
        "version": post.advertisement_processing_version,
        "job_id": post.advertisement_job_id,
        "result": result_document,
        "failure": failure_document,
    }


def _advertisement_result_from_document(value: object) -> AdvertisementCheckResult:
    document = _require_mapping(value, rule="invalid_advertisement_result")
    _require_exact_fields(document, _ADVERTISEMENT_RESULT_FIELDS)
    if type(document["is_advertisement"]) is not bool:
        raise InvalidPostDocumentError
    if type(document["cache_hit"]) is not bool:
        raise InvalidPostDocumentError
    return AdvertisementCheckResult(
        is_advertisement=document["is_advertisement"],
        confidence=_require_float(document["confidence"]),
        reason=_require_string(document["reason"]),
        provider_name=_require_string(document["provider_name"]),
        model_name=_require_string(document["model_name"]),
        checked_at=_restore_floor_datetime(
            document["checked_at"],
            document["checked_at_microsecond_remainder"],
        ),
        prompt_version=_require_string(document["prompt_version"]),
        schema_version=_require_string(document["schema_version"]),
        attempt_number=_require_integer(document["attempt_number"]),
        fallback_count=_require_integer(document["fallback_count"]),
        cache_hit=document["cache_hit"],
        cache_age_seconds=_require_float(document["cache_age_seconds"], optional=True),
    )


def _advertisement_failure_from_document(
    value: object,
) -> AdvertisementCheckFailure:
    document = _require_mapping(value, rule="invalid_advertisement_failure")
    _require_exact_fields(document, _ADVERTISEMENT_FAILURE_FIELDS)
    next_retry_at = None
    if document["next_retry_at"] is not None:
        next_retry_at = _restore_floor_datetime(
            document["next_retry_at"],
            document["next_retry_at_microsecond_remainder"],
        )
    elif _require_integer(document["next_retry_at_microsecond_remainder"]) != 0:
        raise InvalidPostDocumentError
    return AdvertisementCheckFailure(
        policy=AdvertisementFailurePolicy(_require_string(document["policy"])),
        failure_category=_require_string(document["failure_category"]),
        failure_type=_require_string(document["failure_type"]),
        failed_at=_restore_floor_datetime(
            document["failed_at"],
            document["failed_at_microsecond_remainder"],
        ),
        attempted_candidates_count=_require_integer(
            document["attempted_candidates_count"]
        ),
        retry_count=_require_integer(document["retry_count"]),
        fallback_count=_require_integer(document["fallback_count"]),
        next_retry_at=next_retry_at,
    )


def _advertisement_processing_from_document(
    value: object,
) -> tuple[
    AdvertisementProcessingState,
    int,
    str | None,
    AdvertisementCheckResult | None,
    AdvertisementCheckFailure | None,
]:
    document = _require_mapping(value, rule="invalid_advertisement_processing")
    _require_exact_fields(document, _ADVERTISEMENT_FIELDS)
    result = (
        None
        if document["result"] is None
        else _advertisement_result_from_document(document["result"])
    )
    failure = (
        None
        if document["failure"] is None
        else _advertisement_failure_from_document(document["failure"])
    )
    return (
        AdvertisementProcessingState(_require_string(document["state"])),
        _require_integer(document["version"]),
        _require_string(document["job_id"], optional=True),
        result,
        failure,
    )


def semantic_duplicate_processing_to_document(post: Post) -> dict[str, object]:
    """Serialize additive semantic state for full and atomic writes."""
    result_document = None
    if post.semantic_duplicate_result is not None:
        result = post.semantic_duplicate_result
        checked_at, remainder = _floor_to_millisecond(result.checked_at)
        result_document = {
            "method": result.method,
            "is_duplicate": result.is_duplicate,
            "similarity": result.similarity,
            "confidence": result.confidence,
            "matched_post_id": (
                result.matched_post_id.value if result.matched_post_id else None
            ),
            "reason": result.reason,
            "provider_name": result.provider_name,
            "model_name": result.model_name,
            "checked_at": checked_at,
            "checked_at_microsecond_remainder": remainder,
            "prompt_version": result.prompt_version,
            "schema_version": result.schema_version,
            "attempt_number": result.attempt_number,
            "fallback_count": result.fallback_count,
            "cache_hit": result.cache_hit,
            "cache_age_seconds": result.cache_age_seconds,
        }
    failure_document = None
    if post.semantic_duplicate_failure is not None:
        failure = post.semantic_duplicate_failure
        failed_at, failed_remainder = _floor_to_millisecond(failure.failed_at)
        retry_at = None
        retry_remainder = 0
        if failure.next_retry_at is not None:
            retry_at, retry_remainder = _floor_to_millisecond(failure.next_retry_at)
        failure_document = {
            "policy": failure.policy.value,
            "failure_category": failure.failure_category,
            "failed_at": failed_at,
            "failed_at_microsecond_remainder": failed_remainder,
            "next_retry_at": retry_at,
            "next_retry_at_microsecond_remainder": retry_remainder,
        }
    return {
        "state": post.semantic_duplicate_state.value,
        "version": post.semantic_duplicate_version,
        "job_id": post.semantic_duplicate_job_id,
        "result": result_document,
        "failure": failure_document,
    }


def _semantic_result_from_document(value: object) -> SemanticDuplicateResult:
    document = _require_mapping(value, rule="invalid_semantic_duplicate_result")
    _require_exact_fields(document, _SEMANTIC_RESULT_FIELDS)
    if (
        type(document["is_duplicate"]) is not bool
        or type(document["cache_hit"]) is not bool
    ):
        raise InvalidPostDocumentError
    matched = _require_string(document["matched_post_id"], optional=True)
    return SemanticDuplicateResult(
        method=_require_string(document["method"]),
        is_duplicate=document["is_duplicate"],
        similarity=_require_float(document["similarity"]),
        confidence=_require_float(document["confidence"]),
        matched_post_id=PostId(matched) if matched is not None else None,
        reason=_require_string(document["reason"]),
        provider_name=_require_string(document["provider_name"]),
        model_name=_require_string(document["model_name"]),
        checked_at=_restore_floor_datetime(
            document["checked_at"], document["checked_at_microsecond_remainder"]
        ),
        prompt_version=_require_string(document["prompt_version"]),
        schema_version=_require_string(document["schema_version"]),
        attempt_number=_require_integer(document["attempt_number"]),
        fallback_count=_require_integer(document["fallback_count"]),
        cache_hit=document["cache_hit"],
        cache_age_seconds=_require_float(document["cache_age_seconds"], optional=True),
    )


def _semantic_failure_from_document(value: object) -> SemanticDuplicateFailure:
    document = _require_mapping(value, rule="invalid_semantic_duplicate_failure")
    _require_exact_fields(document, _SEMANTIC_FAILURE_FIELDS)
    retry_at = None
    if document["next_retry_at"] is not None:
        retry_at = _restore_floor_datetime(
            document["next_retry_at"], document["next_retry_at_microsecond_remainder"]
        )
    return SemanticDuplicateFailure(
        policy=SemanticDuplicateFailurePolicy(_require_string(document["policy"])),
        failure_category=_require_string(document["failure_category"]),
        failed_at=_restore_floor_datetime(
            document["failed_at"], document["failed_at_microsecond_remainder"]
        ),
        next_retry_at=retry_at,
    )


def _semantic_processing_from_document(
    value: object,
) -> tuple[
    SemanticDuplicateState,
    int,
    str | None,
    SemanticDuplicateResult | None,
    SemanticDuplicateFailure | None,
]:
    document = _require_mapping(value, rule="invalid_semantic_duplicate_processing")
    _require_exact_fields(document, _SEMANTIC_FIELDS)
    return (
        SemanticDuplicateState(_require_string(document["state"])),
        _require_integer(document["version"]),
        _require_string(document["job_id"], optional=True),
        None
        if document["result"] is None
        else _semantic_result_from_document(document["result"]),
        None
        if document["failure"] is None
        else _semantic_failure_from_document(document["failure"]),
    )


def categorization_processing_to_document(post: Post) -> dict[str, object]:
    """Serialize additive categorization state for full and atomic writes."""
    result_document = None
    if post.categorization_result is not None:
        result = post.categorization_result
        assigned_at, remainder = _floor_to_millisecond(result.assigned_at)
        result_document = {
            "category_id": result.category_id,
            "method": result.method.value,
            "policy_version": result.policy_version,
            "assigned_at": assigned_at,
            "assigned_at_microsecond_remainder": remainder,
            "rule_id": result.rule_id,
            "reason": result.reason,
            "confidence": result.confidence,
            "provider_name": result.provider_name,
            "model_name": result.model_name,
            "prompt_version": result.prompt_version,
            "schema_version": result.schema_version,
            "attempt_number": result.attempt_number,
            "fallback_count": result.fallback_count,
            "cache_hit": result.cache_hit,
            "cache_age": result.cache_age,
        }
    failure_document = None
    if post.categorization_failure is not None:
        failure = post.categorization_failure
        failed_at, failed_remainder = _floor_to_millisecond(failure.failed_at)
        retry_at = None
        retry_remainder = 0
        if failure.next_retry_at is not None:
            retry_at, retry_remainder = _floor_to_millisecond(failure.next_retry_at)
        failure_document = {
            "policy": failure.policy,
            "failure_category": failure.failure_category,
            "failed_at": failed_at,
            "failed_at_microsecond_remainder": failed_remainder,
            "attempted_candidates_count": failure.attempted_candidates_count,
            "retry_count": failure.retry_count,
            "fallback_count": failure.fallback_count,
            "next_retry_at": retry_at,
            "next_retry_at_microsecond_remainder": retry_remainder,
        }
    return {
        "state": post.categorization_state.value,
        "version": post.categorization_processing_version,
        "job_id": post.categorization_job_id,
        "result": result_document,
        "failure": failure_document,
    }


def scoring_processing_to_document(post: Post) -> dict[str, object]:
    """Serialize additive delayed-scoring state without raw AI payloads."""
    due_at = None
    due_remainder = 0
    if post.scoring_due_at is not None:
        due_at, due_remainder = _floor_to_millisecond(post.scoring_due_at)
    result_document = None
    if post.scoring_result is not None:
        result = post.scoring_result
        scored_at, scored_remainder = _floor_to_millisecond(result.scored_at)
        result_document = {
            "score": result.score,
            "confidence": result.confidence,
            "reason": result.reason,
            "provider_name": result.provider_name,
            "model_name": result.model_name,
            "scored_at": scored_at,
            "scored_at_microsecond_remainder": scored_remainder,
            "prompt_version": result.prompt_version,
            "schema_version": result.schema_version,
            "attractiveness_probability": result.attractiveness_probability,
            "engagement_probability": result.engagement_probability,
            "headline_quality": result.headline_quality,
            "freshness": result.freshness,
            "news_value": result.news_value,
            "writing_quality": result.writing_quality,
            "cache_hit": result.cache_hit,
            "cache_age_seconds": result.cache_age_seconds,
            "attempt_number": result.attempt_number,
            "fallback_count": result.fallback_count,
        }
    failure_document = None
    if post.scoring_failure is not None:
        failure = post.scoring_failure
        failed_at, failed_remainder = _floor_to_millisecond(failure.failed_at)
        retry_at = None
        retry_remainder = 0
        if failure.next_retry_at is not None:
            retry_at, retry_remainder = _floor_to_millisecond(failure.next_retry_at)
        failure_document = {
            "policy": failure.policy.value,
            "failure_category": failure.failure_category,
            "failed_at": failed_at,
            "failed_at_microsecond_remainder": failed_remainder,
            "next_retry_at": retry_at,
            "next_retry_at_microsecond_remainder": retry_remainder,
        }
    return {
        "state": post.scoring_state.value,
        "version": post.scoring_processing_version,
        "job_id": post.scoring_job_id,
        "due_at": due_at,
        "due_at_microsecond_remainder": due_remainder,
        "result": result_document,
        "failure": failure_document,
    }


def _scoring_processing_from_document(
    value: object,
) -> tuple[
    ScoringState,
    int,
    str | None,
    datetime | None,
    ScoringResult | None,
    ScoringFailure | None,
]:
    document = _require_mapping(value, rule="invalid_scoring_processing")
    _require_exact_fields(document, _SCORING_FIELDS)
    due_at = None
    if document["due_at"] is not None:
        due_at = _restore_floor_datetime(
            document["due_at"], document["due_at_microsecond_remainder"]
        )
    result = None
    if document["result"] is not None:
        item = _require_mapping(document["result"], rule="invalid_scoring_result")
        _require_exact_fields(item, _SCORING_RESULT_FIELDS)
        result = ScoringResult(
            score=_require_integer(item["score"]),
            confidence=_require_float(item["confidence"]),
            reason=_require_string(item["reason"]),
            provider_name=_require_string(item["provider_name"]),
            model_name=_require_string(item["model_name"]),
            scored_at=_restore_floor_datetime(
                item["scored_at"], item["scored_at_microsecond_remainder"]
            ),
            prompt_version=_require_string(item["prompt_version"]),
            schema_version=_require_string(item["schema_version"]),
            attractiveness_probability=_require_float(
                item["attractiveness_probability"], optional=True
            ),
            engagement_probability=_require_float(
                item["engagement_probability"], optional=True
            ),
            headline_quality=None
            if item["headline_quality"] is None
            else _require_integer(item["headline_quality"]),
            freshness=None
            if item["freshness"] is None
            else _require_integer(item["freshness"]),
            news_value=None
            if item["news_value"] is None
            else _require_integer(item["news_value"]),
            writing_quality=None
            if item["writing_quality"] is None
            else _require_integer(item["writing_quality"]),
            cache_hit=_require_boolean(item["cache_hit"]),
            cache_age_seconds=_require_float(item["cache_age_seconds"], optional=True),
            attempt_number=None
            if item["attempt_number"] is None
            else _require_integer(item["attempt_number"]),
            fallback_count=None
            if item["fallback_count"] is None
            else _require_integer(item["fallback_count"]),
        )
    failure = None
    if document["failure"] is not None:
        item = _require_mapping(document["failure"], rule="invalid_scoring_failure")
        _require_exact_fields(item, _SCORING_FAILURE_FIELDS)
        retry_at = None
        if item["next_retry_at"] is not None:
            retry_at = _restore_floor_datetime(
                item["next_retry_at"], item["next_retry_at_microsecond_remainder"]
            )
        failure = ScoringFailure(
            policy=ScoringFailurePolicy(_require_string(item["policy"])),
            failure_category=_require_string(item["failure_category"]),
            failed_at=_restore_floor_datetime(
                item["failed_at"], item["failed_at_microsecond_remainder"]
            ),
            next_retry_at=retry_at,
        )
    return (
        ScoringState(_require_string(document["state"])),
        _require_integer(document["version"]),
        _require_string(document["job_id"], optional=True),
        due_at,
        result,
        failure,
    )


def _categorization_result_from_document(
    document: Mapping[str, object],
) -> CategorizationResult:
    _require_exact_fields(document, _CATEGORIZATION_RESULT_FIELDS)
    return CategorizationResult(
        category_id=_require_string(document["category_id"]),
        method=CategorizationMethod(_require_string(document["method"])),
        policy_version=_require_integer(document["policy_version"]),
        assigned_at=_restore_floor_datetime(
            document["assigned_at"],
            document["assigned_at_microsecond_remainder"],
        ),
        rule_id=_require_string(document.get("rule_id"), optional=True),
        reason=_require_string(document.get("reason"), optional=True),
        confidence=_require_float(document.get("confidence"), optional=True),
        provider_name=_require_string(document.get("provider_name"), optional=True),
        model_name=_require_string(document.get("model_name"), optional=True),
        prompt_version=_require_string(document.get("prompt_version"), optional=True),
        schema_version=_require_string(document.get("schema_version"), optional=True),
        attempt_number=None
        if document.get("attempt_number") is None
        else _require_integer(document["attempt_number"]),
        fallback_count=None
        if document.get("fallback_count") is None
        else _require_integer(document["fallback_count"]),
        cache_hit=_require_boolean(document.get("cache_hit"), optional=True),
        cache_age=_require_float(document.get("cache_age"), optional=True),
    )


def _categorization_failure_from_document(
    document: Mapping[str, object],
) -> CategorizationCheckFailure:
    _require_exact_fields(document, _CATEGORIZATION_FAILURE_FIELDS)
    retry_at = None
    if document.get("next_retry_at") is not None:
        retry_at = _restore_floor_datetime(
            document["next_retry_at"],
            document["next_retry_at_microsecond_remainder"],
        )
    return CategorizationCheckFailure(
        policy=_require_string(document["policy"]),
        failure_category=_require_string(document["failure_category"]),
        failed_at=_restore_floor_datetime(
            document["failed_at"],
            document["failed_at_microsecond_remainder"],
        ),
        attempted_candidates_count=_require_integer(
            document.get("attempted_candidates_count", 0)
        ),
        retry_count=_require_integer(document.get("retry_count", 0)),
        fallback_count=_require_integer(document.get("fallback_count", 0)),
        next_retry_at=retry_at,
    )


def _categorization_processing_from_document(
    value: object,
) -> tuple[
    CategorizationState,
    int,
    str | None,
    CategorizationResult | None,
    CategorizationCheckFailure | None,
]:
    document = _require_mapping(value, rule="invalid_categorization_processing")
    _require_exact_fields(document, _CATEGORIZATION_FIELDS)
    return (
        CategorizationState(_require_string(document["state"])),
        _require_integer(document["version"]),
        _require_string(document["job_id"], optional=True),
        None
        if document["result"] is None
        else _categorization_result_from_document(
            _require_mapping(document["result"], rule="invalid_categorization_result")
        ),
        None
        if document["failure"] is None
        else _categorization_failure_from_document(
            _require_mapping(document["failure"], rule="invalid_categorization_failure")
        ),
    )


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
        "next_stage_claimed_at": None,
        "next_stage_claim_correlation_id": None,
        "advertisement_processing": advertisement_processing_to_document(post),
        "semantic_duplicate_processing": semantic_duplicate_processing_to_document(
            post
        ),
        "categorization_processing": categorization_processing_to_document(post),
        "scoring_processing": scoring_processing_to_document(post),
    }


def post_from_document(value: object) -> Post:
    """Reconstruct and validate a post from the exact version-1 document schema."""
    raw_document = _require_mapping(value, rule="invalid_document")
    document = dict(raw_document)
    has_claim_time = "next_stage_claimed_at" in document
    has_claim_correlation = "next_stage_claim_correlation_id" in document
    if has_claim_time is not has_claim_correlation:
        raise InvalidPostDocumentError("invalid_claim")
    if not has_claim_time:
        document["next_stage_claimed_at"] = None
        document["next_stage_claim_correlation_id"] = None
    if "advertisement_processing" not in document:
        document["advertisement_processing"] = {
            "state": AdvertisementProcessingState.NOT_REQUESTED.value,
            "version": 0,
            "job_id": None,
            "result": None,
            "failure": None,
        }
    if "semantic_duplicate_processing" not in document:
        document["semantic_duplicate_processing"] = {
            "state": SemanticDuplicateState.NOT_REQUESTED.value,
            "version": 0,
            "job_id": None,
            "result": None,
            "failure": None,
        }
    if "categorization_processing" not in document:
        document["categorization_processing"] = {
            "state": CategorizationState.NOT_REQUESTED.value,
            "version": 0,
            "job_id": None,
            "result": None,
            "failure": None,
        }
    if "scoring_processing" not in document:
        document["scoring_processing"] = {
            "state": ScoringState.NOT_REQUESTED.value,
            "version": 0,
            "job_id": None,
            "due_at": None,
            "due_at_microsecond_remainder": 0,
            "result": None,
            "failure": None,
        }
    _require_exact_fields(document, _ROOT_FIELDS)
    if (
        document["schema_version"] != POST_DOCUMENT_SCHEMA_VERSION
        or type(document["schema_version"]) is not int
    ):
        raise InvalidPostDocumentError("invalid_schema_version")
    history_value = document["transition_history"]
    if type(history_value) is not list:
        raise InvalidPostDocumentError
    claimed_at = document["next_stage_claimed_at"]
    claim_correlation_id = document["next_stage_claim_correlation_id"]
    if (claimed_at is None) is not (claim_correlation_id is None):
        raise InvalidPostDocumentError("invalid_claim")
    if claimed_at is not None:
        _canonical_document_datetime(claimed_at)
        _require_string(claim_correlation_id)
    expires_at = _restore_ceil_datetime(
        document["expires_at"],
        document["expires_at_microsecond_remainder"],
    )
    advertisement = _advertisement_processing_from_document(
        document["advertisement_processing"]
    )
    semantic = _semantic_processing_from_document(
        document["semantic_duplicate_processing"]
    )
    categorization = _categorization_processing_from_document(
        document["categorization_processing"]
    )
    scoring = _scoring_processing_from_document(document["scoring_processing"])
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
            advertisement_state=advertisement[0],
            advertisement_processing_version=advertisement[1],
            advertisement_job_id=advertisement[2],
            advertisement_result=advertisement[3],
            advertisement_failure=advertisement[4],
            semantic_duplicate_state=semantic[0],
            semantic_duplicate_version=semantic[1],
            semantic_duplicate_job_id=semantic[2],
            semantic_duplicate_result=semantic[3],
            semantic_duplicate_failure=semantic[4],
            categorization_state=categorization[0],
            categorization_processing_version=categorization[1],
            categorization_job_id=categorization[2],
            categorization_result=categorization[3],
            categorization_failure=categorization[4],
            scoring_state=scoring[0],
            scoring_processing_version=scoring[1],
            scoring_job_id=scoring[2],
            scoring_due_at=scoring[3],
            scoring_result=scoring[4],
            scoring_failure=scoring[5],
        )
    except (AdvertisementDomainError, PostDomainError, ValueError):
        raise InvalidPostDocumentError from None
    if post.expires_at != expires_at:
        raise InvalidPostDocumentError("invalid_expiration")
    return post


__all__ = [
    "POST_DOCUMENT_SCHEMA_VERSION",
    "InvalidPostDocumentError",
    "advertisement_processing_to_document",
    "categorization_processing_to_document",
    "post_from_document",
    "post_to_document",
    "scoring_processing_to_document",
    "semantic_duplicate_processing_to_document",
    "status_transition_to_document",
]
