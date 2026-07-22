"""Pure immutable category values and audit results."""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Category:
    """Separate stable category identity from its display label."""

    category_id: str
    display_name: str
    active: bool = True

    def __post_init__(self) -> None:
        """Validate stable identity and non-empty display text."""
        if not self.category_id or not self.display_name:
            raise ValueError("Category values must not be empty.")


class CategorizationMethod(StrEnum):
    """Identify the deterministic source of a category assignment."""

    MANUAL = "Manual"
    KEYWORD = "Keyword"
    SOURCE_DEFAULT = "SourceDefault"
    AI = "AI"


class CategorizationState(StrEnum):
    """Track categorization processing state independently of Post lifecycle."""

    NOT_REQUESTED = "NotRequested"
    PENDING = "CategorizationPending"
    AI_ASSIGNED = "AiAssigned"
    KEYWORD_FALLBACK = "KeywordFallback"
    SOURCE_DEFAULT_FALLBACK = "SourceDefaultFallback"
    RETRY_PENDING = "CategorizationRetryPending"
    SUPERSEDED_MANUAL = "SupersededByManualOverride"
    PROCESSING_STOPPED = "CategorizationProcessingStopped"


@dataclass(frozen=True, slots=True)
class CategorizationCheckFailure:
    """Persist AI categorization failure details."""

    policy: str
    failure_category: str
    failed_at: datetime
    attempted_candidates_count: int = 0
    retry_count: int = 0
    fallback_count: int = 0
    next_retry_at: datetime | None = None

    def __post_init__(self) -> None:
        """Validate that datetime is timezone-aware and canonicalize to UTC."""
        if not self.policy or not self.failure_category:
            raise ValueError("Categorization failure metadata is invalid.")
        if self.failed_at.tzinfo is None or self.failed_at.utcoffset() is None:
            raise ValueError("Failure time must be timezone-aware.")
        object.__setattr__(self, "failed_at", self.failed_at.astimezone(UTC))
        if self.next_retry_at is not None:
            if (
                self.next_retry_at.tzinfo is None
                or self.next_retry_at.utcoffset() is None
            ):
                raise ValueError("Next retry time must be timezone-aware.")
            object.__setattr__(
                self, "next_retry_at", self.next_retry_at.astimezone(UTC)
            )


@dataclass(frozen=True, slots=True)
class CategorizationResult:
    """Audit one immutable category assignment (manual, baseline or AI)."""

    category_id: str
    method: CategorizationMethod
    policy_version: int
    assigned_at: datetime
    rule_id: str | None = None
    reason: str | None = None
    confidence: float | None = None
    provider_name: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    schema_version: str | None = None
    cache_hit: bool | None = None
    cache_age: float | None = None
    attempt_number: int | None = None
    fallback_count: int | None = None

    def __post_init__(self) -> None:
        """Validate version, identity and timezone-aware audit time."""
        if not self.category_id or self.policy_version <= 0:
            raise ValueError("Categorization result is invalid.")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Categorization confidence is invalid.")
        if self.reason is not None and len(self.reason) > 500:
            raise ValueError("Categorization reason is too long.")
        if self.method is CategorizationMethod.AI and any(
            value is None
            for value in (
                self.confidence,
                self.provider_name,
                self.model_name,
                self.prompt_version,
                self.schema_version,
            )
        ):
            raise ValueError("AI categorization metadata is incomplete.")
        if self.assigned_at.tzinfo is None or self.assigned_at.utcoffset() is None:
            raise ValueError("Categorization time must be timezone-aware.")
        object.__setattr__(self, "assigned_at", self.assigned_at.astimezone(UTC))
