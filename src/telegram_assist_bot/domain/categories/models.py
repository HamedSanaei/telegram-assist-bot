"""Pure immutable category values and audit results."""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


@dataclass(frozen=True, slots=True)
class Category:
    """Separate stable category identity from its display label."""

    category_id: str
    display_name: str

    def __post_init__(self) -> None:
        """Validate stable identity and non-empty display text."""
        if not self.category_id or not self.display_name:
            raise ValueError("Category values must not be empty.")


class CategorizationMethod(StrEnum):
    """Identify the deterministic source of a category assignment."""

    MANUAL = "Manual"
    KEYWORD = "Keyword"
    SOURCE_DEFAULT = "SourceDefault"


@dataclass(frozen=True, slots=True)
class CategorizationResult:
    """Audit one immutable baseline or manual category assignment."""

    category_id: str
    method: CategorizationMethod
    policy_version: int
    assigned_at: datetime
    rule_id: str | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate version, identity and timezone-aware audit time."""
        if not self.category_id or self.policy_version <= 0:
            raise ValueError("Categorization result is invalid.")
        if self.assigned_at.tzinfo is None or self.assigned_at.utcoffset() is None:
            raise ValueError("Categorization time must be timezone-aware.")
