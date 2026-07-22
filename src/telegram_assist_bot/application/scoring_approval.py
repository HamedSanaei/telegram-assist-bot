"""Adapt delayed scoring persistence to the existing approval synchronizer."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class ScoringHeaderSynchronizer(Protocol):
    """Expose the scoring-specific fresh-state approval refresh boundary."""

    async def synchronize_scoring_header(
        self, *, post_id: str, version: int, now: datetime
    ) -> None:
        """Refresh every live approval reference independently."""
        ...


@dataclass(frozen=True, slots=True)
class ApprovalScoringFanout:
    """Invoke the established approval synchronization flow after score CAS."""

    synchronizer: ScoringHeaderSynchronizer = field(repr=False)

    async def execute(self, *, post_id: str, version: int, now: datetime) -> None:
        """Refresh only approval control messages from current stored state."""
        await self.synchronizer.synchronize_scoring_header(
            post_id=post_id,
            version=version,
            now=now,
        )


__all__ = ("ApprovalScoringFanout", "ScoringHeaderSynchronizer")
