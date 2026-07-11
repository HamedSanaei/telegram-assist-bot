"""Handle one mapped live Telegram text event for a canonical source."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    TextMessageIngestor,
)

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import TelegramTextMessage


class LiveMessageOutcome(StrEnum):
    """Describe the payload-free result of one live event."""

    CREATED = "Created"
    ALREADY_EXISTS = "AlreadyExists"
    SKIPPED_OTHER_SOURCE = "SkippedOtherSource"
    SKIPPED_SERVICE = "SkippedService"
    SKIPPED_EMPTY = "SkippedEmpty"
    SKIPPED_MEDIA_ONLY = "SkippedMediaOnly"


@dataclass(frozen=True, slots=True)
class HandleLiveMessage:
    """Filter one live DTO and persist exact text through the repository port."""

    ingestor: TextMessageIngestor = field(repr=False)

    async def execute(
        self,
        message: TelegramTextMessage,
        *,
        source_channel_id: int,
        correlation_id: str,
    ) -> LiveMessageOutcome:
        """Handle one target-source text or caption event idempotently."""
        if message.source_channel_id != source_channel_id:
            return LiveMessageOutcome.SKIPPED_OTHER_SOURCE
        if message.is_service:
            return LiveMessageOutcome.SKIPPED_SERVICE
        has_text = message.text is not None and message.text != ""
        has_caption = message.caption is not None and message.caption != ""
        if not has_text and not has_caption:
            return (
                LiveMessageOutcome.SKIPPED_MEDIA_ONLY
                if message.has_media
                else LiveMessageOutcome.SKIPPED_EMPTY
            )
        result = await self.ingestor.execute(
            message,
            correlation_id=correlation_id,
        )
        if result.outcome is IngestionOutcome.CREATED:
            return LiveMessageOutcome.CREATED
        if result.outcome is IngestionOutcome.ALREADY_EXISTS:
            return LiveMessageOutcome.ALREADY_EXISTS
        raise RuntimeError("Live message ingestion conflicted with stored source data.")


__all__ = ("HandleLiveMessage", "LiveMessageOutcome")
