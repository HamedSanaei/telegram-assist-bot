"""Immutable exact-duplicate result models."""

from dataclasses import dataclass
from datetime import datetime

from telegram_assist_bot.domain.posts import PostId


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
