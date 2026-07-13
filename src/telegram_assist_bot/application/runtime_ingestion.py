"""Shared runtime orchestration for Telegram Post, media, and preparation."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    IngestionResult,
    TextMessageIngestor,
)
from telegram_assist_bot.application.ports import (
    InvalidMediaGroupRecordError,
    MediaDownloadSpec,
    MediaGroup,
    MediaGroupMember,
)
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
    InvalidLogEventError,
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


class AlbumFinalizationDataError(Exception):
    """Report one expected, safely categorized album-data failure."""

    def __init__(self, category: str) -> None:
        """Retain only a code-owned failure category."""
        self.category = category
        super().__init__("Album finalization data is not currently usable.")


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
    album_finalization_owner: str = field(default="album-finalizer", repr=False)
    album_finalization_max_attempts: int = 3
    album_finalization_retry_delay: timedelta = timedelta(seconds=5)
    album_finalization_lease: timedelta = timedelta(minutes=5)

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
        grouped = {
            descriptor.media_group_id
            for descriptor in message.media
            if descriptor.media_group_id is not None
        }
        group_id: str | None = None
        if grouped:
            group_id = next(iter(grouped))
            if len(grouped) != 1 or any(
                descriptor.media_group_id != group_id for descriptor in message.media
            ):
                raise ValueError("Telegram media-group metadata is inconsistent.")
            await self.assembler.observe_member(
                source_channel_id=message.source_channel_id,
                telegram_group_id=group_id,
                source_message_id=message.source_message_id,
                observed_at=now,
            )
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
                    "failure_category": getattr(error, "error_category", "permanent"),
                    "failure_type": type(error).__name__,
                },
                is_failure=True,
            )
            raise

        if group_id is not None:
            if len(stored) != len(message.media):
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
                        observed_at=now,
                        telegram_group_id=group_id,
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
        """Claim and isolate each due Album while preserving loop availability."""
        prepared = 0
        for _ in range(limit):
            now = self.clock.utc_now()
            try:
                group = await self.content_repository.claim_due_group(
                    now=now,
                    owner=self.album_finalization_owner,
                    lease_until=now + self.album_finalization_lease,
                )
            except InvalidMediaGroupRecordError as error:
                await self._isolate_album_failure(
                    group_key=error.group_key,
                    source_channel_id=None,
                    source_message_id=None,
                    media_member_count=error.media_member_count,
                    attempt_count=error.attempt_count,
                    category="invalid_persisted_group",
                    error=error,
                    now=now,
                )
                continue
            if group is None:
                break
            try:
                await self._finalize_claimed_group(group, now=now)
            except asyncio.CancelledError:
                raise
            except AlbumFinalizationDataError as error:
                await self._isolate_album_failure(
                    group_key=group.group_key,
                    source_channel_id=group.source_channel_id,
                    source_message_id=group.canonical_source_message_id,
                    media_member_count=len(group.members),
                    attempt_count=group.attempt_count,
                    category=error.category,
                    error=error,
                    now=now,
                )
                continue
            prepared += 1
        return prepared

    async def _finalize_claimed_group(
        self, group: MediaGroup, *, now: datetime
    ) -> None:
        """Validate one claim, prepare its stable anchor, and complete it."""
        if not group.members:
            raise AlbumFinalizationDataError("missing_media_member")
        represented_ids = {member.source_message_id for member in group.members}
        if not set(group.observed_message_ids).issubset(represented_ids):
            raise AlbumFinalizationDataError("incomplete_media_group")
        for member in group.members:
            identity = member.media.identity
            if (
                member.source_message_id <= 0
                or identity.source_channel_id != group.source_channel_id
                or identity.source_message_id != member.source_message_id
                or (
                    member.telegram_group_id is not None
                    and member.telegram_group_id != group.telegram_group_id
                )
            ):
                raise AlbumFinalizationDataError("invalid_member_identity")
        if group.source_channel_id == 0:
            raise AlbumFinalizationDataError("invalid_source_identity")
        anchor_id = group.canonical_source_message_id
        if anchor_id is None:
            anchor_id = min(
                group.members,
                key=lambda member: (member.source_date, member.source_message_id),
            ).source_message_id
        if anchor_id not in represented_ids:
            raise AlbumFinalizationDataError("invalid_anchor_identity")
        source_identity = SourceMessageIdentity(group.source_channel_id, anchor_id)
        post = await self.post_repository.get_by_source_identity(
            source_identity, as_of=now
        )
        if post is None:
            raise AlbumFinalizationDataError("missing_source_post")
        if post.source_identity != source_identity:
            raise AlbumFinalizationDataError("inconsistent_source_post")
        caption_member = next(
            (member for member in group.members if member.caption is not None),
            next(
                member
                for member in group.members
                if member.source_message_id == anchor_id
            ),
        )
        source_policy = self.policy.source(group.source_channel_id)
        try:
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
                    member.media.content_hash for member in group.members
                ),
                now=now,
                source_channel_id=group.source_channel_id,
                source_message_id=anchor_id,
                correlation_id=self.correlation_id,
            )
        except InvalidLogEventError:
            raise
        except ValueError as error:
            raise AlbumFinalizationDataError("invalid_album_content") from error
        completed = await self.content_repository.complete_group_finalization(
            group.group_key,
            owner=self.album_finalization_owner,
            at=now,
            canonical_source_message_id=anchor_id,
        )
        if not completed:
            raise RuntimeError("Album finalization claim ownership was lost.")
        if group.attempt_count > 1:
            self._emit_album_event(
                "album_finalization_recovered",
                group_key=group.group_key,
                source_channel_id=group.source_channel_id,
                source_message_id=anchor_id,
                media_member_count=len(group.members),
                attempt_count=group.attempt_count,
            )
        self._emit_album_event(
            "album_finalization_completed",
            group_key=group.group_key,
            source_channel_id=group.source_channel_id,
            source_message_id=anchor_id,
            media_member_count=len(group.members),
            attempt_count=group.attempt_count,
        )

    async def _isolate_album_failure(
        self,
        *,
        group_key: str,
        source_channel_id: int | None,
        source_message_id: int | None,
        media_member_count: int,
        attempt_count: int,
        category: str,
        error: BaseException,
        now: datetime,
    ) -> None:
        """Persist one safe group-local retry or terminal failure."""
        self._emit_album_event(
            "album_finalization_failed",
            group_key=group_key,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            media_member_count=media_member_count,
            attempt_count=attempt_count,
            failure_category=category,
            failure_type=type(error).__name__,
        )
        if attempt_count >= self.album_finalization_max_attempts:
            await self.content_repository.fail_group_finalization(
                group_key,
                owner=self.album_finalization_owner,
                at=now,
                failure_category=category,
            )
            self._emit_album_event(
                "album_finalization_permanent_failed",
                group_key=group_key,
                source_channel_id=source_channel_id,
                source_message_id=source_message_id,
                media_member_count=media_member_count,
                attempt_count=attempt_count,
                failure_category=category,
                failure_type=type(error).__name__,
            )
            return
        await self.content_repository.defer_group_finalization(
            group_key,
            owner=self.album_finalization_owner,
            next_attempt_at=now + self.album_finalization_retry_delay,
            failure_category=category,
        )
        self._emit_album_event(
            "album_finalization_deferred",
            group_key=group_key,
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            media_member_count=media_member_count,
            attempt_count=attempt_count,
            failure_category=category,
            failure_type=type(error).__name__,
        )

    def _emit_album_event(
        self,
        event_name: str,
        *,
        group_key: str,
        source_channel_id: int | None,
        source_message_id: int | None,
        media_member_count: int,
        attempt_count: int,
        failure_category: str | None = None,
        failure_type: str | None = None,
    ) -> None:
        """Emit allowlisted album metadata without content or provider details."""
        context = CorrelationContext(
            correlation_id=self.correlation_id,
            channel_id=source_channel_id,
        )
        fields: dict[str, object] = {
            "media_group_identifier": group_key,
            "source_message_identifier": source_message_id,
            "media_member_count": media_member_count,
            "attempt_count": attempt_count,
        }
        if failure_category is not None:
            fields["failure_category"] = failure_category
        if failure_type is not None:
            fields["failure_type"] = failure_type
        with bind_log_context(context):
            self.logger.emit(
                level=(
                    LogLevel.WARNING
                    if event_name != "album_finalization_completed"
                    else LogLevel.INFO
                ),
                event_name=event_name,
                fields=fields,
            )

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
                        "failure_category": getattr(
                            error, "error_category", "permanent"
                        ),
                        "failure_type": type(error).__name__,
                    },
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
        is_failure: bool = False,
    ) -> None:
        context = CorrelationContext(
            correlation_id=correlation_id,
            post_id=result.post_id.value,
            channel_id=message.source_channel_id,
        )
        with bind_log_context(context):
            self.logger.emit(
                level=LogLevel.ERROR if is_failure else LogLevel.INFO,
                event_name=event_name,
                fields={"source_message_id": message.source_message_id, **fields},
            )


__all__ = (
    "RuntimeMessageIngestor",
    "RuntimePreparationPolicy",
    "RuntimeSourcePolicy",
)
