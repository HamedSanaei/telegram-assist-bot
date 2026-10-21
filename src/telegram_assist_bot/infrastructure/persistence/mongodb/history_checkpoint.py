"""Atomic additive MongoDB history cursors and individual failure acknowledgments."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pymongo.errors import DuplicateKeyError

from telegram_assist_bot.application.ports.history_checkpoint import HistoryCheckpoint

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

type Document = dict[str, Any]


@dataclass(frozen=True, slots=True)
class MongoHistoryCheckpointRepository:
    """Fence concurrent writers by source revision using MongoDB's unique _id."""

    checkpoints: AsyncCollection[Document]
    failures: AsyncCollection[Document]

    async def get(self, source_channel_id: int) -> HistoryCheckpoint | None:
        """Load a crash-resumable fixed snapshot with timezone-aware dates."""
        document = await self.checkpoints.find_one({"_id": source_channel_id})
        if document is None:
            return None
        return HistoryCheckpoint(
            source_channel_id=source_channel_id,
            start_inclusive=_utc(document["start_inclusive"]),
            end_exclusive=_utc(document["end_exclusive"]),
            revision=int(document["revision"]),
            offset_message_id=int(document["offset_message_id"]),
            previous_high_message_id=int(document["previous_high_message_id"]),
            high_message_id=int(document["high_message_id"]),
            completed=bool(document["completed"]),
            ordered=bool(document["ordered"]),
        )

    async def save(
        self, checkpoint: HistoryCheckpoint, *, expected_revision: int | None
    ) -> bool:
        """Prevent stale or losing traversals from advancing another cursor."""
        document = asdict(checkpoint)
        document["_id"] = checkpoint.source_channel_id
        if expected_revision is None:
            try:
                await self.checkpoints.insert_one(document)
            except DuplicateKeyError:
                return False
            return True
        result = await self.checkpoints.replace_one(
            {"_id": checkpoint.source_channel_id, "revision": expected_revision},
            document,
        )
        return result.matched_count == 1

    async def is_terminal_failure(
        self, source_channel_id: int, source_message_id: int
    ) -> bool:
        """Look up one stable source identity without materializing failure history."""
        return (
            await self.failures.find_one(
                {"_id": f"{source_channel_id}:{source_message_id}"}, {"_id": 1}
            )
            is not None
        )

    async def record_terminal_failure(
        self,
        source_channel_id: int,
        source_message_id: int,
        *,
        category: str,
        recorded_at: datetime,
    ) -> None:
        """Acknowledge permanent failures independently before advancing a page."""
        await self.failures.update_one(
            {"_id": f"{source_channel_id}:{source_message_id}"},
            {
                "$setOnInsert": {
                    "source_channel_id": source_channel_id,
                    "source_message_id": source_message_id,
                    "category": category,
                    "recorded_at": recorded_at,
                }
            },
            upsert=True,
        )


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
