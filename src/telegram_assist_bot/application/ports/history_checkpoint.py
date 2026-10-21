"""Durable history progress independent from live-message high water marks."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


@dataclass(frozen=True, slots=True)
class HistoryCheckpoint:
    """Advance a snapshot cursor only after every page item is acknowledged."""

    source_channel_id: int
    start_inclusive: datetime
    end_exclusive: datetime
    revision: int = 0
    offset_message_id: int = 0
    previous_high_message_id: int = 0
    high_message_id: int = 0
    completed: bool = False
    ordered: bool = True


class HistoryCheckpointRepository(Protocol):
    """Use compare-and-set progress and independently durable terminal failures."""

    async def get(self, source_channel_id: int) -> HistoryCheckpoint | None:
        """Return the last persisted traversal, including interrupted snapshots."""
        ...

    async def save(
        self, checkpoint: HistoryCheckpoint, *, expected_revision: int | None
    ) -> bool:
        """Insert or atomically replace only the expected source revision."""
        ...

    async def is_terminal_failure(
        self, source_channel_id: int, source_message_id: int
    ) -> bool:
        """Return whether an individual permanent failure was acknowledged."""
        ...

    async def record_terminal_failure(
        self,
        source_channel_id: int,
        source_message_id: int,
        *,
        category: str,
        recorded_at: datetime,
    ) -> None:
        """Idempotently retain safe failure identity without exception payloads."""
        ...
