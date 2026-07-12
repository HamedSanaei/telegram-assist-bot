"""Shared runtime orchestration for Telegram Post, media, and preparation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    IngestionResult,
    TextMessageIngestor,
)
from telegram_assist_bot.application.ports import MediaDownloadSpec, MediaGroupMember
from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparationInput,
    PreparePostPipeline,
)
from telegram_assist_bot.domain.media import MediaIdentity, StoredMedia
from telegram_assist_bot.domain.posts import (
    POST_RETENTION_PERIOD,
    SourceMessageIdentity,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    bind_log_context,
)

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
    from telegram_assist_bot.application.categorize_post import KeywordCategoryRule
    from telegram_assist_bot.application.download_post_media import DownloadPostMedia
    from telegram_assist_bot.application.ports import (
        Clock,
        ContentPreparationRepository,
        MediaStorage,
        PostRepository,
        TelegramTextMessage,
    )
    from telegram_assist_bot.domain.categories import Category
    from telegram_assist_bot.domain.posts import TelegramEntity
    from telegram_assist_bot.shared.retry import RetryEventLogger


@dataclass(frozen=True, slots=True)
class RuntimeSourcePolicy:
    """Hold validated deterministic preparation policy for one source."""

    source_channel_id: int
    source_username: str
    default_category_id: str
    destinations: tuple[DestinationSpec, ...]


@dataclass(frozen=True, slots=True)
class RuntimePreparationPolicy:
    """Hold startup-validated category and destination configuration."""

    categories: tuple[Category, ...]
    category_rules: tuple[KeywordCategoryRule, ...]
    sources: tuple[RuntimeSourcePolicy, ...]

    def source(self, channel_id: int) -> RuntimeSourcePolicy:
        """Return the exact canonical source policy or fail safely."""
        for source in self.sources:
            if source.source_channel_id == channel_id:
                return source
        raise ValueError("No content-preparation policy exists for the source.")


@dataclass(slots=True)
class RuntimeMessageIngestor:
    """Resume the implemented durable stages after canonical Post ingestion."""

    post_ingestor: TextMessageIngestor = field(repr=False)
    post_repository: PostRepository = field(repr=False)
    content_repository: ContentPreparationRepository = field(repr=False)
    storage: MediaStorage = field(repr=False)
    downloader: DownloadPostMedia = field(repr=False)
    assembler: AssembleMediaGroup = field(repr=False)
    pipeline: PreparePostPipeline = field(repr=False)
    policy: RuntimePreparationPolicy
    clock: Clock = field(repr=False)
    logger: RetryEventLogger = field(repr=False)
    correlation_id: str = field(repr=False)

    async def execute(
        self,
        message: TelegramTextMessage,
        *,
        correlation_id: str,
    ) -> IngestionResult:
        """Persist one delivery and resume only its incomplete durable stages."""
        result = await self.post_ingestor.execute(
            message, correlation_id=correlation_id
        )
        if result.outcome is IngestionOutcome.CONFLICT:
            return result
        source_policy = self.policy.source(message.source_channel_id)
        now = self.clock.utc_now()
        stored: list[StoredMedia] = []
        if message.media:
            self._emit(
                "media_ingestion_started",
                message,
                result,
                correlation_id=correlation_id,
                fields={"media_item_count": len(message.media)},
            )
        try:
            for descriptor in message.media:
                identity = MediaIdentity(
                    message.source_channel_id,
                    message.source_message_id,
                    descriptor.item_index,
                )
                existing = await self.content_repository.get_media(identity)
                was_reusable = existing is not None and await self.storage.exists(
                    existing.storage_path
                )
                media = await self.downloader.execute(
                    MediaDownloadSpec(
                        identity=identity,
                        media_type=descriptor.media_type,
                        opaque_reference=descriptor.opaque_reference,
                        mime_type=descriptor.mime_type,
                        original_filename=descriptor.original_filename,
                        expires_at=now + POST_RETENTION_PERIOD,
                    )
                )
                stored.append(media)
                self._emit(
                    "media_download_reused"
                    if was_reusable
                    else "media_download_succeeded",
                    message,
                    result,
                    correlation_id=correlation_id,
                    fields={"media_type": descriptor.media_type.value},
                )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._emit(
                "media_download_failed",
                message,
                result,
                correlation_id=correlation_id,
                fields={
                    "error_category": getattr(error, "error_category", "permanent")
                },
                error=error,
            )
            raise

        grouped = {
            descriptor.media_group_id
            for descriptor in message.media
            if descriptor.media_group_id is not None
        }
        if grouped:
            group_id = next(iter(grouped))
            if len(grouped) != 1 or len(stored) != len(message.media):
                raise ValueError("Telegram media-group metadata is inconsistent.")
            for descriptor, media in zip(message.media, stored, strict=True):
                if descriptor.media_group_id != group_id:
                    raise ValueError("Telegram media-group metadata is inconsistent.")
                group = await self.assembler.add_member(
                    source_channel_id=message.source_channel_id,
                    telegram_group_id=group_id,
                    member=MediaGroupMember(
                        source_message_id=message.source_message_id,
                        source_date=message.source_published_at,
                        media=media,
                        caption=message.caption,
                        caption_entities=message.caption_entities,
                    ),
                )
            self._emit(
                "media_group_updated",
                message,
                result,
                correlation_id=correlation_id,
                fields={"media_item_count": len(group.members)},
            )
            return result

        await self._prepare(
            result=result,
            source_policy=source_policy,
            text=message.text,
            caption=message.caption,
            entities=(
                message.text_entities
                if message.text is not None
                else message.caption_entities
            ),
            media_hashes=tuple(item.content_hash for item in stored),
            now=now,
            source_channel_id=message.source_channel_id,
            source_message_id=message.source_message_id,
            correlation_id=correlation_id,
        )
        return result

    async def finalize_due_groups(self, *, limit: int = 100) -> int:
        """Finalize and prepare one bounded persisted Album batch."""
        now = self.clock.utc_now()
        groups = await self.content_repository.list_due_groups(now=now, limit=limit)
        prepared = 0
        for group in groups:
            if not await self.assembler.finalize_if_due(group.group_key, now=now):
                continue
            finalized = await self.content_repository.get_group(group.group_key)
            if finalized is None or not finalized.members:
                continue
            caption_member = next(
                (member for member in finalized.members if member.caption is not None),
                finalized.members[0],
            )
            post = await self.post_repository.get_by_source_identity(
                SourceMessageIdentity(
                    finalized.source_channel_id, caption_member.source_message_id
                ),
                as_of=now,
            )
            if post is None:
                continue
            source_policy = self.policy.source(finalized.source_channel_id)
            await self._prepare(
                result=IngestionResult(
                    IngestionOutcome.ALREADY_EXISTS, post.post_id, False
                ),
                source_policy=source_policy,
                text=post.original_content.text,
                caption=caption_member.caption,
                entities=(
                    post.original_content.text_entities
                    if post.original_content.text is not None
                    else caption_member.caption_entities
                ),
                media_hashes=tuple(
                    member.media.content_hash for member in finalized.members
                ),
                now=now,
                source_channel_id=finalized.source_channel_id,
                source_message_id=caption_member.source_message_id,
                correlation_id=self.correlation_id,
            )
            prepared += 1
        return prepared

    async def _prepare(
        self,
        *,
        result: IngestionResult,
        source_policy: RuntimeSourcePolicy,
        text: str | None,
        caption: str | None,
        entities: tuple[TelegramEntity, ...],
        media_hashes: tuple[str, ...],
        now: datetime,
        source_channel_id: int,
        source_message_id: int,
        correlation_id: str,
    ) -> None:
        context = CorrelationContext(
            correlation_id=correlation_id,
            post_id=result.post_id.value,
            channel_id=source_channel_id,
        )
        with bind_log_context(context):
            self.logger.emit(
                level=LogLevel.INFO,
                event_name="content_preparation_started",
                fields={
                    "source_message_id": source_message_id,
                    "media_item_count": len(media_hashes),
                },
            )
        try:
            await self.pipeline.execute(
                PreparationInput(
                    post_id=result.post_id,
                    text=text,
                    caption=caption,
                    entities=entities,
                    source_username=source_policy.source_username,
                    media_hashes=media_hashes,
                    categories=self.policy.categories,
                    category_rules=self.policy.category_rules,
                    source_default_category_id=source_policy.default_category_id,
                    destinations=source_policy.destinations,
                    now=now,
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as error:
            with bind_log_context(context):
                self.logger.emit(
                    level=LogLevel.ERROR,
                    event_name="content_preparation_failed",
                    fields={
                        "source_message_id": source_message_id,
                        "error_category": getattr(error, "error_category", "permanent"),
                    },
                    error=error,
                )
            raise
        with bind_log_context(context):
            self.logger.emit(
                level=LogLevel.INFO,
                event_name="content_preparation_ready",
                fields={
                    "source_message_id": source_message_id,
                    "media_item_count": len(media_hashes),
                },
            )

    def _emit(
        self,
        event_name: str,
        message: TelegramTextMessage,
        result: IngestionResult,
        *,
        correlation_id: str,
        fields: dict[str, object],
        error: BaseException | None = None,
    ) -> None:
        context = CorrelationContext(
            correlation_id=correlation_id,
            post_id=result.post_id.value,
            channel_id=message.source_channel_id,
        )
        with bind_log_context(context):
            self.logger.emit(
                level=LogLevel.ERROR if error is not None else LogLevel.INFO,
                event_name=event_name,
                fields={"source_message_id": message.source_message_id, **fields},
                error=error,
            )


__all__ = (
    "RuntimeMessageIngestor",
    "RuntimePreparationPolicy",
    "RuntimeSourcePolicy",
)
