"""Immutable exact and semantic duplicate result models."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from telegram_assist_bot.domain.posts import PostId

SEMANTIC_DUPLICATE_MANUAL_REVIEW_REASON: Final[str] = "semantic_duplicate_detected"


class InvalidSemanticDuplicateTransitionError(Exception):
    """Reject stale or backward semantic processing transitions."""


class SemanticDuplicatePolicy(StrEnum):
    """Approved actions for a validated semantic duplicate."""

    REJECT = "reject"
    MANUAL_REVIEW = "manual_review"
    CONTINUE_PROCESSING = "continue_processing"


class SemanticDuplicateFailurePolicy(StrEnum):
    """Application actions for a semantic AI failure."""

    CONTINUE_PROCESSING = "continue_processing"
    STOP_PROCESSING = "stop_processing"
    RETRY_LATER = "retry_later"
    MANUAL_REVIEW = "manual_review"


class SemanticDuplicateState(StrEnum):
    """Track semantic processing without overloading Post lifecycle."""

    NOT_REQUESTED = "NotRequested"
    PENDING = "SemanticDuplicatePending"
    RETRY_PENDING = "SemanticDuplicateRetryPending"
    PASSED = "SemanticDuplicatePassed"
    DUPLICATE_REJECTED = "RejectedAsSemanticDuplicate"
    DUPLICATE_MANUAL_REVIEW = "SemanticDuplicateManualReviewRequired"
    DUPLICATE_ALLOWED = "SemanticDuplicateAllowed"
    FAILURE_CONTINUE = "SemanticDuplicateFailureContinue"
    PROCESSING_STOPPED = "SemanticDuplicateProcessingStopped"
    FAILURE_MANUAL_REVIEW = "SemanticDuplicateFailureManualReview"

    @property
    def is_terminal(self) -> bool:
        """Return whether another semantic completion may not move the Post."""
        return self not in {self.NOT_REQUESTED, self.PENDING, self.RETRY_PENDING}

    @property
    def allows_next_pipeline_stage(self) -> bool:
        """Return whether categorization may be entered exactly once."""
        return self in {self.PASSED, self.DUPLICATE_ALLOWED, self.FAILURE_CONTINUE}

    @property
    def requires_manual_review(self) -> bool:
        """Return whether the approval application may consume this Post."""
        return self in {self.DUPLICATE_MANUAL_REVIEW, self.FAILURE_MANUAL_REVIEW}


def _canonical_utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise ValueError("Semantic duplicate time must be timezone-aware.")
    offset = value.utcoffset()
    if offset is None:
        raise ValueError("Semantic duplicate time must be timezone-aware.")
    return value.astimezone(UTC)


def _bounded(value: object, maximum: int = 128) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= maximum
    )


@dataclass(frozen=True, slots=True)
class DuplicateCheckResult:
    """Record one deterministic versioned exact-duplicate decision."""

    is_duplicate: bool
    matched_post_id: PostId | None
    method: str
    normalization_version: int
    hash_version: int
    content_hash: str
    checked_at: datetime

    def __post_init__(self) -> None:
        """Validate match consistency and timezone-aware audit time."""
        if self.is_duplicate != (self.matched_post_id is not None):
            raise ValueError("Duplicate match identity is inconsistent.")
        if self.checked_at.tzinfo is None or self.checked_at.utcoffset() is None:
            raise ValueError("Duplicate check time must be timezone-aware.")


@dataclass(frozen=True, slots=True)
class SemanticDuplicateResult:
    """Record one validated provider-independent semantic decision."""

    is_duplicate: bool
    similarity: float
    confidence: float
    matched_post_id: PostId | None
    reason: str
    provider_name: str
    model_name: str
    checked_at: datetime
    prompt_version: str
    schema_version: str
    attempt_number: int
    fallback_count: int
    cache_hit: bool = False
    cache_age_seconds: float | None = None
    method: str = "semantic"

    def __post_init__(self) -> None:
        """Validate identity and scores without rewriting visible Persian text."""
        if type(self.is_duplicate) is not bool:
            raise ValueError("Invalid semantic duplicate Boolean.")
        if type(self.similarity) is not float or not 0.0 <= self.similarity <= 1.0:
            raise ValueError("Invalid semantic similarity.")
        if type(self.confidence) is not float or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Invalid semantic confidence.")
        if self.is_duplicate != (self.matched_post_id is not None):
            raise ValueError("Semantic matched Post identity is inconsistent.")
        if self.method != "semantic":
            raise ValueError("Invalid semantic duplicate method.")
        if not _bounded(self.reason, 512) or any(
            ord(char) < 32 for char in self.reason
        ):
            raise ValueError("Invalid semantic duplicate reason.")
        if not all(
            _bounded(value)
            for value in (
                self.provider_name,
                self.model_name,
                self.prompt_version,
                self.schema_version,
            )
        ):
            raise ValueError("Invalid semantic duplicate metadata.")
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise ValueError("Invalid semantic attempt number.")
        if type(self.fallback_count) is not int or self.fallback_count < 0:
            raise ValueError("Invalid semantic fallback count.")
        if type(self.cache_hit) is not bool:
            raise ValueError("Invalid semantic cache metadata.")
        if self.cache_age_seconds is not None and (
            type(self.cache_age_seconds) is not float or self.cache_age_seconds < 0
        ):
            raise ValueError("Invalid semantic cache age.")
        object.__setattr__(self, "checked_at", _canonical_utc(self.checked_at))


@dataclass(frozen=True, slots=True)
class SemanticDuplicateFailure:
    """Persist sanitized AI failure metadata without a fake non-match."""

    policy: SemanticDuplicateFailurePolicy
    failure_category: str
    failed_at: datetime
    next_retry_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate only stable safe codes and future retry timing."""
        if type(self.policy) is not SemanticDuplicateFailurePolicy or not _bounded(
            self.failure_category
        ):
            raise ValueError("Invalid semantic failure metadata.")
        object.__setattr__(self, "failed_at", _canonical_utc(self.failed_at))
        if self.next_retry_at is not None:
            retry = _canonical_utc(self.next_retry_at)
            if retry <= self.failed_at:
                raise ValueError("Semantic retry must be in the future.")
            object.__setattr__(self, "next_retry_at", retry)
