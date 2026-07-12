"""Verify the shared Post, media, Album, and preparation runtime path."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    IngestionResult,
)
from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    PostRepository,
    TelegramMediaReference,
    TelegramTextMessage,
)
from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparePostPipeline,
)
from telegram_assist_bot.application.runtime_ingestion import (
    RuntimeMessageIngestor,
    RuntimePreparationPolicy,
    RuntimeSourcePolicy,
)
from telegram_assist_bot.application.text_ingestion import build_stored_post
from telegram_assist_bot.domain.categories import Category
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import Post, PostId, SourceMessageIdentity
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from tests.unit.application.m2_fakes import FakePreparationRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from telegram_assist_bot.application.ingest_post_idempotently import (
        TextMessageIngestor,
    )
    from telegram_assist_bot.shared.retry import RetryEventLogger


@dataclass
class Clock:
    """Provide a mutable deterministic UTC instant."""

    now: datetime

    def utc_now(self) -> datetime:
        return self.now


@dataclass
class PostStore:
    """Retain canonical Posts for Album recovery in unit tests."""

    posts: dict[SourceMessageIdentity, Post] = field(default_factory=dict)

    async def get_by_source_identity(
        self, identity: SourceMessageIdentity, *, as_of: datetime
    ) -> Post | None:
        del as_of
        return self.posts.get(identity)


@dataclass
class BaseIngestor:
    """Create deterministic canonical Post snapshots without external I/O."""

    clock: Clock
    store: PostStore

    async def execute(
        self, message: TelegramTextMessage, *, correlation_id: str
    ) -> IngestionResult:
        del correlation_id
        identity = SourceMessageIdentity(
            message.source_channel_id, message.source_message_id
        )
        existing = self.store.posts.get(identity)
        if existing is not None:
            return IngestionResult(
                IngestionOutcome.ALREADY_EXISTS, existing.post_id, False
            )
        post = build_stored_post(
            message,
            received_at=self.clock.now,
            post_id_factory=lambda value: PostId(
                f"post-{value.source_channel_id}-{value.source_message_id}"
            ),
        )
        self.store.posts[identity] = post
        return IngestionResult(IngestionOutcome.CREATED, post.post_id, True)


class Source:
    """Return synthetic streams while exposing bounded attempt counts."""

    def __init__(self, payload: bytes = b"media", *, permanent: bool = False) -> None:
        self.payload = payload
        self.permanent = permanent
        self.opens = 0

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        self.opens += 1
        if self.permanent:
            raise MediaPermanentError("Synthetic permanent media failure.")
        assert opaque_reference.startswith("opaque-")

        async def stream() -> AsyncIterator[bytes]:
            yield self.payload[:2]
            yield self.payload[2:]

        return stream()


@dataclass
class Logger:
    """Capture structured event fields without formatting payloads."""

    events: list[dict[str, object]] = field(default_factory=list)

    def emit(self, **event: object) -> None:
        self.events.append(dict(event))


def message(
    message_id: int,
    published_at: datetime,
    *,
    media_type: MediaType | None = None,
    group_id: str | None = None,
    caption: str | None = None,
) -> TelegramTextMessage:
    """Build one exact provider-neutral Telegram fixture."""
    media = (
        (
            TelegramMediaReference(
                media_type,
                0,
                5,
                "application/octet-stream",
                "نام امن.bin",
                f"opaque-{message_id}",
                group_id,
            ),
        )
        if media_type is not None
        else ()
    )
    return TelegramTextMessage(
        source_channel_id=-100,
        source_channel_username="source_name",
        source_channel_display_name="منبع فارسی",
        source_message_id=message_id,
        text="متن‌فارسی\n😀" if media_type is None else None,
        caption=caption if media_type is not None else None,
        text_entities=(),
        caption_entities=(),
        source_published_at=published_at,
        is_service=False,
        has_media=media_type is not None,
        media=media,
    )


def coordinator(
    tmp_path: Path,
    clock: Clock,
    *,
    source: Source | None = None,
) -> tuple[RuntimeMessageIngestor, FakePreparationRepository, Source, Logger]:
    """Build the real Application pipeline over deterministic boundaries."""
    preparation = FakePreparationRepository()
    post_store = PostStore()
    media_source = source or Source()
    storage = LocalMediaStorage(tmp_path / "media")
    logger = Logger()
    use_case = RuntimeMessageIngestor(
        post_ingestor=BaseIngestor(clock, post_store),
        post_repository=cast("PostRepository", post_store),
        content_repository=preparation,
        storage=storage,
        downloader=DownloadPostMedia(
            media_source,
            storage,
            preparation,
            maximum_bytes=100,
            timeout_seconds=1,
            maximum_attempts=2,
        ),
        assembler=AssembleMediaGroup(
            preparation,
            quiet_window=timedelta(seconds=2),
            maximum_wait=timedelta(seconds=10),
        ),
        pipeline=PreparePostPipeline(preparation),
        policy=RuntimePreparationPolicy(
            categories=(Category("general", "عمومی"),),
            category_rules=(),
            sources=(
                RuntimeSourcePolicy(
                    -100,
                    "source_name",
                    "general",
                    (DestinationSpec("destination", "destination_name"),),
                ),
            ),
        ),
        clock=clock,
        logger=cast("RetryEventLogger", logger),
        correlation_id="runtime-correlation",
    )
    return use_case, preparation, media_source, logger


def test_text_and_single_media_prepare_idempotently_with_safe_events(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
        use_case, repository, source, logger = coordinator(tmp_path, Clock(now))
        text = message(1, now)
        photo = message(2, now, media_type=MediaType.PHOTO, caption="کپشن‌اصلی\n✨")

        await use_case.execute(text, correlation_id="test-correlation")
        await use_case.execute(photo, correlation_id="test-correlation")
        await use_case.execute(photo, correlation_id="test-correlation")

        assert len(repository.media) == 1
        assert len(repository.ready) == 2
        assert source.opens == 1
        stored = next(iter(repository.media.values()))
        assert await LocalMediaStorage(tmp_path / "media").exists(stored.storage_path)
        rendered = repr(logger.events)
        assert "opaque-2" not in rendered
        assert str((tmp_path / "media").resolve()) not in rendered
        assert "media_download_succeeded" in rendered
        assert "media_download_reused" in rendered

    asyncio.run(scenario())


def test_album_waits_then_finalizes_once_in_deterministic_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
        clock = Clock(now)
        use_case, repository, _, _ = coordinator(tmp_path, clock)
        deliveries = (
            message(
                3,
                now - timedelta(seconds=7),
                media_type=MediaType.PHOTO,
                group_id="album",
                caption="سوم",
            ),
            message(
                1,
                now - timedelta(seconds=9),
                media_type=MediaType.PHOTO,
                group_id="album",
                caption="اول‌😀",
            ),
            message(
                2,
                now - timedelta(seconds=8),
                media_type=MediaType.PHOTO,
                group_id="album",
                caption="دوم",
            ),
        )
        for item in deliveries:
            await use_case.execute(item, correlation_id="album-correlation")
        assert repository.ready == set()

        assert await use_case.finalize_due_groups() == 1
        assert await use_case.finalize_due_groups() == 0
        group = repository.groups["-100:album"]
        assert [member.source_message_id for member in group.members] == [1, 2, 3]
        assert repository.ready == {"post--100-1"}
        duplicate = repository.duplicates["post--100-1"]
        assert len(duplicate.content_hash) == 64

    asyncio.run(scenario())


def test_incomplete_album_and_permanent_failure_do_not_create_readiness(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
        use_case, repository, _, _ = coordinator(tmp_path / "album", Clock(now))
        await use_case.execute(
            message(
                1,
                now,
                media_type=MediaType.PHOTO,
                group_id="waiting",
                caption=None,
            ),
            correlation_id="waiting-correlation",
        )
        assert await use_case.finalize_due_groups() == 0
        assert repository.ready == set()

        failing = Source(permanent=True)
        failed_use_case, failed_repository, _, _ = coordinator(
            tmp_path / "failure", Clock(now), source=failing
        )
        with pytest.raises(MediaPermanentError, match="permanent"):
            await failed_use_case.execute(
                message(2, now, media_type=MediaType.DOCUMENT, caption="فایل"),
                correlation_id="failure-correlation",
            )
        assert failing.opens == 1
        assert failed_repository.ready == set()

    asyncio.run(scenario())


def test_runtime_rejects_conflict_invalid_groups_and_preparation_failures(
    tmp_path: Path,
) -> None:
    class ConflictIngestor:
        async def execute(
            self, item: TelegramTextMessage, *, correlation_id: str
        ) -> IngestionResult:
            del item, correlation_id
            return IngestionResult(IngestionOutcome.CONFLICT, PostId("conflict"), False)

    class FailingPipeline:
        def __init__(self, failure: BaseException) -> None:
            self.failure = failure

        async def execute(self, request: object) -> object:
            del request
            raise self.failure

    async def scenario() -> None:
        now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
        use_case, _, _, logger = coordinator(tmp_path, Clock(now))
        original = use_case.post_ingestor
        use_case.post_ingestor = cast("TextMessageIngestor", ConflictIngestor())
        assert (
            await use_case.execute(message(1, now), correlation_id="conflict")
        ).outcome is IngestionOutcome.CONFLICT
        use_case.post_ingestor = original

        with pytest.raises(ValueError, match="No content-preparation policy"):
            use_case.policy.source(-999)

        base = message(2, now, media_type=MediaType.PHOTO, group_id="a", caption="گروه")
        second = TelegramMediaReference(
            MediaType.DOCUMENT,
            1,
            5,
            None,
            None,
            "opaque-2-second",
            "b",
        )
        with pytest.raises(ValueError, match="inconsistent"):
            await use_case.execute(
                replace(base, media=(*base.media, second)),
                correlation_id="invalid-groups",
            )

        second_without_group = replace(second, media_group_id=None)
        with pytest.raises(ValueError, match="inconsistent"):
            await use_case.execute(
                replace(
                    message(
                        3,
                        now,
                        media_type=MediaType.PHOTO,
                        group_id="a",
                        caption="گروه",
                    ),
                    media=(
                        replace(base.media[0], opaque_reference="opaque-3"),
                        replace(
                            second_without_group,
                            opaque_reference="opaque-3-second",
                        ),
                    ),
                ),
                correlation_id="missing-group",
            )

        use_case.pipeline = cast(
            "PreparePostPipeline", FailingPipeline(ValueError("safe"))
        )
        with pytest.raises(ValueError, match="safe"):
            await use_case.execute(message(4, now), correlation_id="pipeline-error")
        assert "content_preparation_failed" in repr(logger.events)

        use_case.pipeline = cast(
            "PreparePostPipeline", FailingPipeline(asyncio.CancelledError())
        )
        with pytest.raises(asyncio.CancelledError):
            await use_case.execute(message(5, now), correlation_id="pipeline-cancel")

    asyncio.run(scenario())
