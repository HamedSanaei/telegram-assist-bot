"""Application-owned persistence contract for disposable AI result cache."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ai.cache_key import AICacheIdentity
    from telegram_assist_bot.application.ai.contracts import AIResult


class AICacheRepositoryError(Exception):
    """Sanitized cache side-effect failure without driver details."""


@dataclass(frozen=True, slots=True)
class AICacheEntry:
    """Validated cache result and safe lifetime metadata."""

    identity: AICacheIdentity
    result: AIResult
    created_at: datetime
    expires_at: datetime
    cache_schema_version: int = 1


@dataclass(frozen=True, slots=True)
class AICacheWriteResult:
    """Return the accepted first valid cache entry and writer outcome."""

    entry: AICacheEntry
    created: bool


class AICacheRepository(Protocol):
    """Read and atomically create validated disposable cache entries."""

    async def get(
        self, identity: AICacheIdentity, *, as_of: datetime
    ) -> AICacheEntry | None:
        """Return only a valid, compatible and unexpired entry."""
        ...

    async def put_if_absent(self, entry: AICacheEntry) -> AICacheWriteResult:
        """Apply deterministic first-valid-write-wins semantics."""
        ...


__all__ = (
    "AICacheEntry",
    "AICacheRepository",
    "AICacheRepositoryError",
    "AICacheWriteResult",
)
