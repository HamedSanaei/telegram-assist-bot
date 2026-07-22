"""Application-owned query contract for semantic duplicate candidates."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.posts import PostId


@dataclass(frozen=True, slots=True)
class SemanticDuplicateCandidate:
    """Expose only identity, normalized comparison text and eligibility times."""

    post_id: PostId
    comparison_text: str
    received_at: datetime
    expires_at: datetime


class SemanticDuplicateCandidateRepository(Protocol):
    """Query deterministic non-expired candidates inside an inclusive window."""

    async def list_candidates(
        self,
        *,
        current_post_id: PostId,
        now: datetime,
        window_start: datetime,
        limit: int,
    ) -> tuple[SemanticDuplicateCandidate, ...]:
        """Return newest-first candidates, breaking ties by Post ID ascending."""
        ...


__all__ = (
    "SemanticDuplicateCandidate",
    "SemanticDuplicateCandidateRepository",
)
