from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import pytest

from telegram_assist_bot.application import (
    CrawlTodayTextPosts,
    IngestPostIdempotently,
    current_local_day_interval,
)
from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    InsertPostResult,
    PostClaimOutcome,
    PostClaimRequest,
    PostClaimResult,
    TelegramHistoryPage,
    TelegramTextMessage,
)
from telegram_assist_bot.domain.posts import (
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TelegramEntity,
)
from telegram_assist_bot.shared.errors import TransientOperationError
from telegram_assist_bot.shared.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine, Mapping

    from telegram_assist_bot.application.ports import PostTransitionRequest
    from telegram_assist_bot.shared.config import LogLevel


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass(frozen=True)
class FixedClock:
    now: datetime

    def utc_now(self) -> datetime:
        return self.now


@dataclass
class FakeHistoryGateway:
    pages: tuple[TelegramHistoryPage, ...]
    failures: list[Exception] = field(default_factory=list)
    queries: list[object] = field(default_factory=list)

    async def iter_history_pages(
        self, query: object
    ) -> AsyncIterator[TelegramHistoryPage]:
        self.queries.append(query)
        if self.failures:
            raise self.failures.pop(0)
        for page in self.pages:
            yield page


@dataclass
class FakeRepository:
    posts: dict[SourceMessageIdentity, Post] = field(default_factory=dict)
    claims: set[PostId] = field(default_factory=set)

    async def insert_idempotently(self, post: Post) -> InsertPostResult:
        if post.source_identity in self.posts:
            existing = self.posts[post.source_identity]
            return InsertPostResult(InsertPostOutcome.ALREADY_EXISTS, existing.post_id)
        self.posts[post.source_identity] = post
        return InsertPostResult(InsertPostOutcome.CREATED, post.post_id)

    async def claim_for_next_stage(self, request: PostClaimRequest) -> PostClaimResult:
        if request.post_id in self.claims:
            return PostClaimResult(PostClaimOutcome.ALREADY_CLAIMED, request.post_id)
        self.claims.add(request.post_id)
        return PostClaimResult(PostClaimOutcome.CLAIMED, request.post_id)

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        del as_of
        return next(
            (post for post in self.posts.values() if post.post_id == post_id), None
        )

    async def get_by_source_identity(
        self,
        source_identity: SourceMessageIdentity,
        *,
        as_of: datetime,
    ) -> Post | None:
        del as_of
        return self.posts.get(source_identity)

    async def list_unexpired(self, *, as_of: datetime, limit: int) -> tuple[Post, ...]:
        del as_of
        return tuple(self.posts.values())[:limit]

    async def transition(self, request: PostTransitionRequest) -> Post:
        return request.post


@dataclass
class RecordingLogger:
    events: list[tuple[str, Mapping[str, object] | None]] = field(default_factory=list)

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        del level, error
        self.events.append((event_name, fields))


async def no_sleep(_delay: float) -> None:
    return None


def message(
    message_id: int,
    published_at: datetime,
    *,
    channel_id: int = -1001,
    text: str | None = "متن‌فارسی\n😀",
    caption: str | None = None,
    service: bool = False,
    media: bool = False,
) -> TelegramTextMessage:
    entity = TelegramEntity(0, 2, "bold") if text else ()
    return TelegramTextMessage(
        source_channel_id=channel_id,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=message_id,
        text=text,
        caption=caption,
        text_entities=(entity,) if isinstance(entity, TelegramEntity) else (),
        caption_entities=(),
        source_published_at=published_at,
        is_service=service,
        has_media=media,
    )


def crawler(
    gateway: FakeHistoryGateway,
    repository: FakeRepository,
    now: datetime,
    *,
    timezone: str = "Asia/Tehran",
    max_attempts: int = 2,
) -> CrawlTodayTextPosts:
    clock = FixedClock(now)
    logger = RecordingLogger()
    ingestor = IngestPostIdempotently(
        repository,
        clock,
        lambda identity: PostId(
            f"post-{identity.source_channel_id}-{identity.source_message_id}"
        ),
        logger,
    )
    return CrawlTodayTextPosts(
        gateway=gateway,
        ingestor=ingestor,
        clock=clock,
        timezone=ZoneInfo(timezone),
        retry_policy=RetryPolicy(max_attempts, 0, 0),
        logger=logger,
        sleeper=no_sleep,
        jitter_source=lambda: 0.5,
        page_size=2,
        max_pages=4,
    )


def test_tehran_local_midnight_is_converted_to_utc() -> None:
    now = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)

    start, end = current_local_day_interval(now, ZoneInfo("Asia/Tehran"))

    assert start == datetime(2026, 7, 10, 20, 30, tzinfo=UTC)
    assert end == now


def test_dst_start_day_uses_offset_at_local_midnight() -> None:
    now = datetime(2026, 3, 8, 12, 0, tzinfo=UTC)

    start, _ = current_local_day_interval(now, ZoneInfo("America/New_York"))

    assert start == datetime(2026, 3, 8, 5, 0, tzinfo=UTC)


def test_crawls_multiple_pages_filters_interval_and_preserves_payload() -> None:
    now = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    start, _ = current_local_day_interval(now, ZoneInfo("Asia/Tehran"))
    gateway = FakeHistoryGateway(
        (
            TelegramHistoryPage((message(1, start), message(2, now))),
            TelegramHistoryPage(
                (
                    message(3, start, service=True),
                    message(4, start, text=None, media=True),
                    message(5, start, channel_id=-1002),
                )
            ),
        )
    )
    repository = FakeRepository()

    result = run(
        crawler(gateway, repository, now).execute(-1001, correlation_id="crawl-1")
    )

    assert result.created == 1
    assert result.skipped_outside_interval == 1
    assert result.skipped_service == 1
    assert result.skipped_media_only == 1
    assert result.skipped_other_source == 1
    stored = next(iter(repository.posts.values()))
    assert stored.status is PostStatus.STORED
    assert stored.original_text == "متن‌فارسی\n😀"
    assert stored.original_text_entities == (TelegramEntity(0, 2, "bold"),)


def test_repeated_crawl_relies_on_repository_idempotency() -> None:
    now = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    start, _ = current_local_day_interval(now, ZoneInfo("Asia/Tehran"))
    gateway = FakeHistoryGateway((TelegramHistoryPage((message(1, start),)),))
    repository = FakeRepository()
    use_case = crawler(gateway, repository, now)

    first = run(use_case.execute(-1001, correlation_id="crawl-1"))
    second = run(use_case.execute(-1001, correlation_id="crawl-2"))

    assert first.created == 1
    assert second.already_existing == 1
    assert len(repository.posts) == 1


def test_transient_history_failure_retries_before_any_write() -> None:
    now = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)
    start, _ = current_local_day_interval(now, ZoneInfo("Asia/Tehran"))
    gateway = FakeHistoryGateway(
        (TelegramHistoryPage((message(1, start),)),),
        failures=[TransientOperationError()],
    )
    repository = FakeRepository()

    result = run(
        crawler(gateway, repository, now).execute(-1001, correlation_id="crawl-1")
    )

    assert result.created == 1
    assert len(gateway.queries) == 2


def test_cancellation_propagates_without_retry() -> None:
    class CancelledIterator:
        def __aiter__(self) -> CancelledIterator:
            return self

        async def __anext__(self) -> TelegramHistoryPage:
            raise asyncio.CancelledError

    class CancelledGateway(FakeHistoryGateway):
        def iter_history_pages(
            self,
            query: object,
        ) -> AsyncIterator[TelegramHistoryPage]:
            del query
            return CancelledIterator()

    now = datetime(2026, 7, 11, 10, 0, tzinfo=UTC)

    with pytest.raises(asyncio.CancelledError):
        run(
            crawler(CancelledGateway(()), FakeRepository(), now).execute(
                -1001,
                correlation_id="crawl-1",
            )
        )
