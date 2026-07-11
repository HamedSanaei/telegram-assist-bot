from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from tests.integration.test_crawl_today_text_posts import (
    MongoTestSettings,
    resources,
)

from telegram_assist_bot.application import HandleLiveMessage, IngestPostIdempotently
from telegram_assist_bot.application.ports import (
    TelegramTextMessage,
    TelegramTransientError,
)
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.shared.retry import RetryPolicy
from telegram_assist_bot.workers import LiveTextListener

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from telegram_assist_bot.shared.config import LogLevel

pytestmark = pytest.mark.integration


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass(frozen=True)
class Clock:
    def utc_now(self) -> datetime:
        return datetime(2099, 3, 20, 8, 0, tzinfo=UTC)


@dataclass
class Subscription:
    items: list[TelegramTextMessage | Exception]
    closed: int = 0

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> TelegramTextMessage:
        if not self.items:
            raise StopAsyncIteration
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self) -> None:
        self.closed += 1


@dataclass
class Gateway:
    subscriptions: list[Subscription]

    async def subscribe(
        self, source_channel_id: int, *, buffer_size: int
    ) -> Subscription:
        assert source_channel_id == -1001
        assert buffer_size == 2
        return self.subscriptions.pop(0)


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


def item(message_id: int) -> TelegramTextMessage:
    return TelegramTextMessage(
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=message_id,
        text="پیام‌زنده\n✨",
        caption=None,
        text_entities=(),
        caption_entities=(),
        source_published_at=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        is_service=False,
        has_media=False,
    )


def test_new_and_duplicate_live_events_create_one_document(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            subscription = Subscription([item(1), item(1)])
            logger = Logger()
            ingestor = IngestPostIdempotently(
                owned.repository,
                Clock(),
                lambda identity: PostId(
                    f"post-{identity.source_channel_id}-{identity.source_message_id}"
                ),
                logger,
            )
            handler = HandleLiveMessage(ingestor)
            listener = LiveTextListener(
                Gateway([subscription]),
                handler,
                RetryPolicy(2, 0, 0),
                logger,
                no_sleep,
                lambda: 0.5,
                2,
                10,
            )

            result = await listener.run(-1001, correlation_id="listener-1")

            assert result.created == 1
            assert result.already_existing == 1
            assert await owned.collection.count_documents({}) == 1
            assert subscription.closed == 1

    run(scenario())


def test_disconnect_reconnect_continues_without_duplicate_or_resource_leak(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            baseline_tasks = set(asyncio.all_tasks())
            first = Subscription([item(1), TelegramTransientError()])
            second = Subscription([item(1), item(2)])
            logger = Logger()
            ingestor = IngestPostIdempotently(
                owned.repository,
                Clock(),
                lambda identity: PostId(
                    f"post-{identity.source_channel_id}-{identity.source_message_id}"
                ),
                logger,
            )
            handler = HandleLiveMessage(ingestor)
            listener = LiveTextListener(
                Gateway([first, second]),
                handler,
                RetryPolicy(2, 0, 0),
                logger,
                no_sleep,
                lambda: 0.5,
                2,
                10,
            )

            result = await listener.run(-1001, correlation_id="listener-1")

            assert result.created == 2
            assert result.already_existing == 1
            assert result.reconnects == 1
            assert await owned.collection.count_documents({}) == 2
            assert first.closed == 1
            assert second.closed == 1
            assert not [
                task
                for task in asyncio.all_tasks()
                if task not in baseline_tasks and not task.done()
            ]

    run(scenario())
