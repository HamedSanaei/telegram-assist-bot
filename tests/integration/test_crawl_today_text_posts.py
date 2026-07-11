from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol
from zoneinfo import ZoneInfo

import pytest

from telegram_assist_bot.application import CrawlTodayTextPosts, IngestPostIdempotently
from telegram_assist_bot.application.ports import (
    TelegramHistoryPage,
    TelegramTextMessage,
)
from telegram_assist_bot.domain.posts import (
    PostId,
    SourceMessageIdentity,
    TelegramEntity,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    initialize_post_indexes,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    LogLevel,
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)
from telegram_assist_bot.shared.errors import TransientOperationError
from telegram_assist_bot.shared.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine, Mapping

    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

pytestmark = pytest.mark.integration

_URI_ENV = "TEST_MONGODB_URI"
_NOW = datetime(2099, 3, 20, 8, 0, tzinfo=UTC)


class MongoTestSettings(Protocol):
    uri: str
    database_name: str


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class Resources:
    client: AsyncMongoClient[MongoDocument]
    collection: AsyncCollection[MongoDocument]
    repository: MongoPostRepository


@asynccontextmanager
async def resources(settings: MongoTestSettings) -> AsyncIterator[Resources]:
    config = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(config, ResolvedSecrets({_URI_ENV: settings.uri}))
    try:
        await verify_mongodb_connection(client, timeout_seconds=5)
        collection = get_posts_collection(client, config)
        await initialize_post_indexes(collection, timeout_seconds=5)
        yield Resources(client, collection, MongoPostRepository(collection, 5))
    finally:
        await close_mongodb_client(client, timeout_seconds=5)


@dataclass(frozen=True)
class Clock:
    def utc_now(self) -> datetime:
        return _NOW


@dataclass
class Gateway:
    pages: tuple[TelegramHistoryPage, ...]
    fail_after_first_page_once: bool = False
    attempts: int = 0

    async def iter_history_pages(
        self, query: object
    ) -> AsyncIterator[TelegramHistoryPage]:
        del query
        self.attempts += 1
        for index, page in enumerate(self.pages):
            yield page
            if self.fail_after_first_page_once and self.attempts == 1 and index == 0:
                raise TransientOperationError


@dataclass
class Logger:
    events: list[str] = field(default_factory=list)

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        del level, fields, error
        self.events.append(event_name)


async def no_sleep(_delay: float) -> None:
    return None


def item(message_id: int, published_at: datetime) -> TelegramTextMessage:
    text = "سلام‌دنیا\nPremium 😀"
    return TelegramTextMessage(
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=message_id,
        text=text,
        caption=None,
        text_entities=(TelegramEntity(12, 2, "custom_emoji", "987654"),),
        caption_entities=(),
        source_published_at=published_at,
        is_service=False,
        has_media=False,
    )


def create_crawler(
    gateway: Gateway, repository: MongoPostRepository
) -> CrawlTodayTextPosts:
    clock = Clock()
    logger = Logger()
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
        timezone=ZoneInfo("Asia/Tehran"),
        retry_policy=RetryPolicy(2, 0, 0),
        logger=logger,
        sleeper=no_sleep,
        jitter_source=lambda: 0.5,
        page_size=2,
        max_pages=4,
    )


def test_crawl_round_trip_and_repeat_are_idempotent(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            gateway = Gateway(
                (TelegramHistoryPage((item(1, _NOW - timedelta(hours=1)),)),)
            )
            crawler = create_crawler(gateway, owned.repository)

            first = await crawler.execute(-1001, correlation_id="crawl-1")
            second = await crawler.execute(-1001, correlation_id="crawl-2")
            stored = await owned.repository.get_by_source_identity(
                SourceMessageIdentity(-1001, 1),
                as_of=_NOW,
            )

            assert first.created == 1
            assert second.already_existing == 1
            assert await owned.collection.count_documents({}) == 1
            assert stored is not None
            assert stored.original_text == "سلام‌دنیا\nPremium 😀"
            assert stored.original_text_entities[0].custom_emoji_id == "987654"
            assert stored.source_published_at == _NOW - timedelta(hours=1)

    run(scenario())


def test_interval_excludes_now_and_includes_local_midnight(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            local_midnight_utc = datetime(2099, 3, 19, 20, 30, tzinfo=UTC)
            gateway = Gateway(
                (
                    TelegramHistoryPage(
                        (
                            item(1, local_midnight_utc),
                            item(2, _NOW),
                            item(3, local_midnight_utc - timedelta(microseconds=1)),
                        )
                    ),
                )
            )

            result = await create_crawler(gateway, owned.repository).execute(
                -1001,
                correlation_id="crawl-1",
            )

            assert result.created == 1
            assert result.skipped_outside_interval == 2
            assert await owned.collection.count_documents({}) == 1

    run(scenario())


def test_failure_between_pages_retries_without_duplicates(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            gateway = Gateway(
                (
                    TelegramHistoryPage((item(1, _NOW - timedelta(hours=2)),)),
                    TelegramHistoryPage((item(2, _NOW - timedelta(hours=1)),)),
                ),
                fail_after_first_page_once=True,
            )

            result = await create_crawler(gateway, owned.repository).execute(
                -1001,
                correlation_id="crawl-1",
            )

            assert result.created == 2
            assert gateway.attempts == 2
            assert await owned.collection.count_documents({}) == 2

    run(scenario())
