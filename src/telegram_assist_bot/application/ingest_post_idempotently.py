"""Shared atomic ingestion path for history and live Telegram producers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.application.ports import (
    Clock,
    InsertPostOutcome,
    PostClaimOutcome,
    PostClaimRequest,
    PostRepository,
    TelegramTextMessage,
)
from telegram_assist_bot.application.text_ingestion import (
    PostIdFactory,
    build_stored_post,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    bind_log_context,
)

if TYPE_CHECKING:
    from telegram_assist_bot.domain.posts import PostId
    from telegram_assist_bot.shared.retry import RetryEventLogger


class IngestionOutcome(StrEnum):
    """Describe the atomic persistence result for one source identity."""

    CREATED = "Created"
    ALREADY_EXISTS = "AlreadyExists"
    CONFLICT = "Conflict"


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Return canonical identity and whether this call won the durable claim."""

    outcome: IngestionOutcome
    post_id: PostId
    downstream_claimed: bool


class TextMessageIngestor(Protocol):
    """Ingest one source DTO through the single application write path."""

    async def execute(
        self,
        message: TelegramTextMessage,
        *,
        correlation_id: str,
    ) -> IngestionResult:
        """Persist and atomically claim one source message."""
        ...


@dataclass(frozen=True, slots=True)
class IngestPostIdempotently:
    """Persist once and set one database-backed next-stage marker."""

    repository: PostRepository = field(repr=False)
    clock: Clock = field(repr=False)
    post_id_factory: PostIdFactory = field(repr=False)
    logger: RetryEventLogger = field(repr=False)

    async def execute(
        self,
        message: TelegramTextMessage,
        *,
        correlation_id: str,
    ) -> IngestionResult:
        """Return Created, AlreadyExists, or Conflict with one canonical Post ID."""
        if (
            type(correlation_id) is not str
            or not correlation_id
            or correlation_id.isspace()
            or len(correlation_id) > 128
        ):
            raise ValueError("correlation_id must be a bounded non-blank string")
        received_at = self.clock.utc_now()
        post = build_stored_post(
            message,
            received_at=received_at,
            post_id_factory=self.post_id_factory,
        )
        inserted = await self.repository.insert_idempotently(post)
        if inserted.outcome is InsertPostOutcome.CONFLICT:
            result = IngestionResult(
                IngestionOutcome.CONFLICT,
                inserted.post_id,
                False,
            )
            self._log_result(result, message, correlation_id=correlation_id)
            return result

        claim = await self.repository.claim_for_next_stage(
            PostClaimRequest(
                post_id=inserted.post_id,
                source_identity=post.source_identity,
                claimed_at=received_at,
                correlation_id=correlation_id,
            )
        )
        outcome = (
            IngestionOutcome.CREATED
            if inserted.outcome is InsertPostOutcome.CREATED
            else IngestionOutcome.ALREADY_EXISTS
        )
        if claim.outcome is PostClaimOutcome.CONFLICT:
            outcome = IngestionOutcome.CONFLICT
        result = IngestionResult(
            outcome=outcome,
            post_id=inserted.post_id,
            downstream_claimed=claim.outcome is PostClaimOutcome.CLAIMED,
        )
        self._log_result(result, message, correlation_id=correlation_id)
        return result

    def _log_result(
        self,
        result: IngestionResult,
        message: TelegramTextMessage,
        *,
        correlation_id: str,
    ) -> None:
        context = CorrelationContext(
            correlation_id=correlation_id,
            post_id=result.post_id.value,
            channel_id=message.source_channel_id,
        )
        with bind_log_context(context):
            self.logger.emit(
                level=LogLevel.INFO,
                event_name="telegram_post_ingested",
                fields={
                    "source_message_id": message.source_message_id,
                    "ingestion_outcome": result.outcome.value,
                    "downstream_claimed": result.downstream_claimed,
                },
            )


__all__ = (
    "IngestPostIdempotently",
    "IngestionOutcome",
    "IngestionResult",
    "TextMessageIngestor",
)
