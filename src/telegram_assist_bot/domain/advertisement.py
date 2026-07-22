"""Provider-independent advertisement-check state and validated results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

_MAX_REASON_LENGTH: Final[int] = 512
_MAX_IDENTITY_LENGTH: Final[int] = 128
ADVERTISEMENT_MANUAL_REVIEW_REASON: Final[str] = "advertisement_check_failed"
_SAFE_FAILURE_CATEGORIES: Final[frozenset[str]] = frozenset(
    {
        "authorization",
        "configuration",
        "concurrency_conflict",
        "unknown",
        "already_completed",
        "permanent",
        "permission",
        "rate_limit",
        "timeout",
        "transient",
        "validation",
    }
)


class AdvertisementDomainError(Exception):
    """Base error for advertisement-check invariants."""


class InvalidAdvertisementResultError(AdvertisementDomainError):
    """Reject malformed or unsafe normalized advertisement results."""


class InvalidAdvertisementTransitionError(AdvertisementDomainError):
    """Reject stale, backward, or unsupported processing transitions."""


class AdvertisementFailurePolicy(StrEnum):
    """Approved business actions for final advertisement-check failure."""

    CONTINUE_PROCESSING = "continue_processing"
    STOP_PROCESSING = "stop_processing"
    RETRY_LATER = "retry_later"
    MANUAL_REVIEW = "manual_review"


class AdvertisementProcessingState(StrEnum):
    """Track advertisement detection independently from the Post lifecycle."""

    NOT_REQUESTED = "NotRequested"
    PENDING = "AdvertisementCheckPending"
    RETRY_PENDING = "AdvertisementCheckRetryPending"
    PASSED = "AdvertisementCheckPassed"
    REJECTED_AS_ADVERTISEMENT = "RejectedAsAdvertisement"
    FAILED_CONTINUE = "AdvertisementCheckFailedContinue"
    PROCESSING_STOPPED = "AdvertisementProcessingStopped"
    MANUAL_REVIEW_REQUIRED = "AdvertisementManualReviewRequired"

    @property
    def is_terminal(self) -> bool:
        """Return whether automated advertisement processing cannot advance again."""
        return self in {
            self.PASSED,
            self.REJECTED_AS_ADVERTISEMENT,
            self.FAILED_CONTINUE,
            self.PROCESSING_STOPPED,
            self.MANUAL_REVIEW_REQUIRED,
        }

    @property
    def allows_next_pipeline_stage(self) -> bool:
        """Return whether the exact next normal pipeline stage may run."""
        return self in {self.PASSED, self.FAILED_CONTINUE}

    @property
    def approval_review_eligible(self) -> bool:
        """Expose the existing approval-flow handoff without defining Bot UX."""
        return self is self.MANUAL_REVIEW_REQUIRED


def _canonical_utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None:
        raise InvalidAdvertisementResultError
    try:
        offset = value.utcoffset()
        if offset is None:
            raise InvalidAdvertisementResultError
        return value.astimezone(UTC)
    except (OverflowError, TypeError, ValueError):
        raise InvalidAdvertisementResultError from None


def _bounded_identity(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= _MAX_IDENTITY_LENGTH
    )


def _safe_reason(value: object) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= _MAX_REASON_LENGTH
        and all(ord(character) >= 32 for character in value)
    )


@dataclass(frozen=True, slots=True)
class AdvertisementCheckResult:
    """Persist only validated standard advertisement-classification data."""

    is_advertisement: bool
    confidence: float
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

    def __post_init__(self) -> None:
        """Validate without rewriting Persian, ZWNJ, Emoji, or punctuation."""
        if type(self.is_advertisement) is not bool:
            raise InvalidAdvertisementResultError
        if type(self.confidence) is not float or not 0.0 <= self.confidence <= 1.0:
            raise InvalidAdvertisementResultError
        if not _safe_reason(self.reason):
            raise InvalidAdvertisementResultError
        if not all(
            _bounded_identity(value)
            for value in (
                self.provider_name,
                self.model_name,
                self.prompt_version,
                self.schema_version,
            )
        ):
            raise InvalidAdvertisementResultError
        if type(self.attempt_number) is not int or self.attempt_number < 1:
            raise InvalidAdvertisementResultError
        if type(self.fallback_count) is not int or self.fallback_count < 0:
            raise InvalidAdvertisementResultError
        if type(self.cache_hit) is not bool:
            raise InvalidAdvertisementResultError
        if self.cache_age_seconds is not None and (
            type(self.cache_age_seconds) is not float or self.cache_age_seconds < 0.0
        ):
            raise InvalidAdvertisementResultError
        object.__setattr__(self, "checked_at", _canonical_utc(self.checked_at))


@dataclass(frozen=True, slots=True)
class AdvertisementCheckFailure:
    """Persist sanitized final-failure metadata without a fabricated result."""

    policy: AdvertisementFailurePolicy
    failure_category: str
    failure_type: str
    failed_at: datetime
    attempted_candidates_count: int
    retry_count: int
    fallback_count: int
    next_retry_at: datetime | None = None

    def __post_init__(self) -> None:
        """Accept only bounded codes and explicit safe retry timing."""
        if type(self.policy) is not AdvertisementFailurePolicy:
            raise InvalidAdvertisementResultError
        if self.failure_category not in _SAFE_FAILURE_CATEGORIES:
            raise InvalidAdvertisementResultError
        if not _bounded_identity(self.failure_type):
            raise InvalidAdvertisementResultError
        for value in (
            self.attempted_candidates_count,
            self.retry_count,
            self.fallback_count,
        ):
            if type(value) is not int or value < 0:
                raise InvalidAdvertisementResultError
        object.__setattr__(self, "failed_at", _canonical_utc(self.failed_at))
        if self.next_retry_at is not None:
            next_retry_at = _canonical_utc(self.next_retry_at)
            if next_retry_at <= self.failed_at:
                raise InvalidAdvertisementResultError
            object.__setattr__(self, "next_retry_at", next_retry_at)


__all__ = (
    "ADVERTISEMENT_MANUAL_REVIEW_REASON",
    "AdvertisementCheckFailure",
    "AdvertisementCheckResult",
    "AdvertisementDomainError",
    "AdvertisementFailurePolicy",
    "AdvertisementProcessingState",
    "InvalidAdvertisementResultError",
    "InvalidAdvertisementTransitionError",
)
