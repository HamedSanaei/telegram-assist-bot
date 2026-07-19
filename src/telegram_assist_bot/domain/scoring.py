"""Provider-independent delayed AI scoring state and validated results."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Final

_MAX_REASON_LENGTH: Final[int] = 512
_MAX_IDENTITY_LENGTH: Final[int] = 128


class ScoringState(StrEnum):
    """Track delayed scoring independently from the foundational Post lifecycle."""

    NOT_REQUESTED = "NotRequested"
    SCHEDULED = "ScoringScheduled"
    PENDING = "ScoringPending"
    RETRY_PENDING = "ScoringRetryPending"
    COMPLETED = "ScoringCompleted"
    UNAVAILABLE = "ScoringUnavailable"
    STALE_OR_EXPIRED = "ScoringStaleOrExpired"

    @property
    def is_terminal(self) -> bool:
        """Return whether scoring may no longer change automatically."""
        return self in {self.COMPLETED, self.UNAVAILABLE, self.STALE_OR_EXPIRED}


class ScoringFailurePolicy(StrEnum):
    """Approved scoring-specific actions after provider exhaustion."""

    RETRY_LATER = "retry_later"
    MARK_UNAVAILABLE = "mark_unavailable"


def _canonical_utc(value: datetime) -> datetime:
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Scoring time must be timezone-aware.")
    return value.astimezone(UTC)


def _bounded_text(value: object, maximum: int) -> bool:
    return (
        type(value) is str
        and bool(value)
        and not value.isspace()
        and len(value) <= maximum
        and all(ord(character) >= 32 for character in value)
    )


@dataclass(frozen=True, slots=True)
class ScoringResult:
    """Persist one validated normalized score without provider-specific payloads."""

    score: int
    confidence: float
    reason: str
    provider_name: str
    model_name: str
    scored_at: datetime
    prompt_version: str
    schema_version: str
    attractiveness_probability: float | None = None
    engagement_probability: float | None = None
    headline_quality: int | None = None
    freshness: int | None = None
    news_value: int | None = None
    writing_quality: int | None = None
    cache_hit: bool = False
    cache_age_seconds: float | None = None
    attempt_number: int | None = None
    fallback_count: int | None = None

    def __post_init__(self) -> None:
        """Enforce strict ranges while preserving Persian, ZWNJ and Emoji."""
        if type(self.score) is not int or not 0 <= self.score <= 100:
            raise ValueError("Scoring score is invalid.")
        if type(self.confidence) is not float or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Scoring confidence is invalid.")
        if not _bounded_text(self.reason, _MAX_REASON_LENGTH):
            raise ValueError("Scoring reason is invalid.")
        for metadata_value in (
            self.provider_name,
            self.model_name,
            self.prompt_version,
            self.schema_version,
        ):
            if not _bounded_text(metadata_value, _MAX_IDENTITY_LENGTH):
                raise ValueError("Scoring metadata is invalid.")
        for probability in (
            self.attractiveness_probability,
            self.engagement_probability,
        ):
            if probability is not None and (
                type(probability) is not float or not 0.0 <= probability <= 1.0
            ):
                raise ValueError("Scoring probability is invalid.")
        for component in (
            self.headline_quality,
            self.freshness,
            self.news_value,
            self.writing_quality,
        ):
            if component is not None and (
                type(component) is not int or not 0 <= component <= 100
            ):
                raise ValueError("Scoring component is invalid.")
        if type(self.cache_hit) is not bool:
            raise ValueError("Scoring cache metadata is invalid.")
        if self.cache_age_seconds is not None and (
            type(self.cache_age_seconds) is not float or self.cache_age_seconds < 0.0
        ):
            raise ValueError("Scoring cache age is invalid.")
        for count in (self.attempt_number, self.fallback_count):
            if count is not None and (type(count) is not int or count < 0):
                raise ValueError("Scoring execution metadata is invalid.")
        object.__setattr__(self, "scored_at", _canonical_utc(self.scored_at))


@dataclass(frozen=True, slots=True)
class ScoringFailure:
    """Persist bounded failure metadata without fabricating a score."""

    policy: ScoringFailurePolicy
    failure_category: str
    failed_at: datetime
    next_retry_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate stable safe codes and optional durable retry timing."""
        if type(self.policy) is not ScoringFailurePolicy or not _bounded_text(
            self.failure_category, _MAX_IDENTITY_LENGTH
        ):
            raise ValueError("Scoring failure metadata is invalid.")
        object.__setattr__(self, "failed_at", _canonical_utc(self.failed_at))
        if self.next_retry_at is not None:
            retry_at = _canonical_utc(self.next_retry_at)
            if retry_at <= self.failed_at:
                raise ValueError("Scoring retry must be in the future.")
            object.__setattr__(self, "next_retry_at", retry_at)


__all__ = (
    "ScoringFailure",
    "ScoringFailurePolicy",
    "ScoringResult",
    "ScoringState",
)
