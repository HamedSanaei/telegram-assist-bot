from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import LiveMessageOutcome
from telegram_assist_bot.application.ports import (
    TelegramGatewayError,
    TelegramRateLimitError,
    TelegramTextMessage,
    TelegramTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user import TelethonLiveAdapter
from telegram_assist_bot.shared.retry import RetryPolicy
from telegram_assist_bot.workers import LiveTextListener

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from telegram_assist_bot.shared.config import LogLevel


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


def message(message_id: int = 1) -> TelegramTextMessage:
    return TelegramTextMessage(
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=message_id,
        text="متن‌زنده\n😀",
        caption=None,
        text_entities=(),
        caption_entities=(),
        source_published_at=datetime(2099, 3, 20, 8, 0, tzinfo=UTC),
        is_service=False,
        has_media=False,
    )


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
class BlockingSubscription(Subscription):
    entered: asyncio.Event | None = None

    async def __anext__(self) -> TelegramTextMessage:
        if self.entered is not None:
            self.entered.set()
        await asyncio.Event().wait()
        raise AssertionError


@dataclass
class Gateway:
    subscriptions: list[Subscription]
    calls: list[tuple[int, int]] = field(default_factory=list)

    async def subscribe(
        self, source_channel_id: int, *, buffer_size: int
    ) -> Subscription:
        self.calls.append((source_channel_id, buffer_size))
        return self.subscriptions.pop(0)


@dataclass
class Handler:
    outcomes: list[LiveMessageOutcome]
    identities: list[int] = field(default_factory=list)

    async def execute(
        self,
        item: TelegramTextMessage,
        *,
        source_channel_id: int,
        correlation_id: str,
    ) -> LiveMessageOutcome:
        assert item.source_channel_id == source_channel_id
        assert correlation_id == "listener-1"
        self.identities.append(item.source_message_id)
        return self.outcomes.pop(0)


@dataclass
class Logger:
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


@dataclass
class SleepRecorder:
    delays: list[float] = field(default_factory=list)

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def worker(
    gateway: Gateway,
    handler: Handler,
    *,
    logger: Logger | None = None,
    sleeper: SleepRecorder | None = None,
    max_attempts: int = 3,
    flood_cap: float = 10,
) -> LiveTextListener:
    return LiveTextListener(
        gateway=gateway,
        handler=handler,
        retry_policy=RetryPolicy(max_attempts, 1, 4),
        logger=logger or Logger(),
        sleeper=sleeper or SleepRecorder(),
        jitter_source=lambda: 0.5,
        buffer_size=2,
        maximum_flood_wait_seconds=flood_cap,
    )


def test_consumes_created_duplicate_and_skipped_outcomes() -> None:
    subscription = Subscription([message(1), message(2), message(3)])
    gateway = Gateway([subscription])
    handler = Handler(
        [
            LiveMessageOutcome.CREATED,
            LiveMessageOutcome.ALREADY_EXISTS,
            LiveMessageOutcome.SKIPPED_SERVICE,
        ]
    )

    result = run(worker(gateway, handler).run(-1001, correlation_id="listener-1"))

    assert (result.created, result.already_existing, result.skipped) == (1, 1, 1)
    assert subscription.closed == 1
    assert gateway.calls == [(-1001, 2)]


def test_transient_disconnect_reconnects_with_bounded_backoff() -> None:
    first = Subscription([TelegramTransientError()])
    second = Subscription([message(2)])
    gateway = Gateway([first, second])
    sleeper = SleepRecorder()
    logger = Logger()

    result = run(
        worker(
            gateway,
            Handler([LiveMessageOutcome.CREATED]),
            sleeper=sleeper,
            logger=logger,
        ).run(-1001, correlation_id="listener-1")
    )

    assert result.reconnects == 1
    assert sleeper.delays == [1.0]
    assert first.closed == 1
    assert second.closed == 1
    assert logger.events[0][0] == "telegram_listener_reconnect_scheduled"


def test_permanent_failure_does_not_reconnect() -> None:
    gateway = Gateway([Subscription([TelegramGatewayError()])])

    with pytest.raises(TelegramGatewayError):
        run(
            worker(gateway, Handler([])).run(
                -1001,
                correlation_id="listener-1",
            )
        )

    assert len(gateway.calls) == 1


def test_flood_wait_uses_reported_delay_within_cap() -> None:
    gateway = Gateway(
        [Subscription([TelegramRateLimitError(3)]), Subscription([message(2)])]
    )
    sleeper = SleepRecorder()

    run(
        worker(
            gateway,
            Handler([LiveMessageOutcome.CREATED]),
            sleeper=sleeper,
        ).run(-1001, correlation_id="listener-1")
    )

    assert sleeper.delays == [3.0]


def test_flood_wait_above_cap_stops_without_sleep_or_reconnect() -> None:
    gateway = Gateway([Subscription([TelegramRateLimitError(11)])])
    sleeper = SleepRecorder()

    with pytest.raises(TelegramRateLimitError):
        run(
            worker(
                gateway,
                Handler([]),
                sleeper=sleeper,
                flood_cap=10,
            ).run(-1001, correlation_id="listener-1")
        )

    assert sleeper.delays == []
    assert len(gateway.calls) == 1


def test_cancellation_unsubscribes_and_propagates() -> None:
    async def scenario() -> None:
        entered = asyncio.Event()
        subscription = BlockingSubscription([], entered=entered)
        listener = worker(Gateway([subscription]), Handler([]))
        task = asyncio.create_task(listener.run(-1001, correlation_id="listener-1"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert subscription.closed == 1

    run(scenario())


def test_telethon_adapter_queue_applies_backpressure_and_unsubscribes() -> None:
    @dataclass
    class Client:
        callback: object | None = None
        builder: object | None = None
        removed: int = 0

        def add_event_handler(self, callback: object, event: object) -> None:
            self.callback = callback
            self.builder = event

        def remove_event_handler(self, callback: object, event: object) -> int:
            assert callback is self.callback
            assert event is self.builder
            self.removed += 1
            return 1

    async def scenario() -> None:
        client = Client()
        subscription = await TelethonLiveAdapter(client, None, "Source").subscribe(
            -1001,
            buffer_size=1,
        )
        callback = client.callback
        assert callable(callback)
        raw = SimpleNamespace(
            id=1,
            date=datetime(2099, 3, 20, 8, 0, tzinfo=UTC),
            message="متن‌زنده",
            entities=[],
            media=None,
            action=None,
        )
        first = asyncio.create_task(callback(SimpleNamespace(message=raw)))
        await first
        raw.id = 2
        second = asyncio.create_task(callback(SimpleNamespace(message=raw)))
        await asyncio.sleep(0)
        assert not second.done()
        assert (await anext(subscription)).source_message_id == 1
        await second
        assert (await anext(subscription)).source_message_id == 2
        await subscription.close()
        await subscription.close()
        assert client.removed == 1

    run(scenario())


def test_telethon_adapter_surfaces_mapping_failure_and_closed_iterator() -> None:
    @dataclass
    class Client:
        callback: object | None = None
        builder: object | None = None

        def add_event_handler(self, callback: object, event: object) -> None:
            self.callback = callback
            self.builder = event

        def remove_event_handler(self, callback: object, event: object) -> int:
            assert callback is self.callback
            assert event is self.builder
            return 1

    async def scenario() -> None:
        client = Client()
        subscription = await TelethonLiveAdapter(client, None, "Source").subscribe(
            -1001,
            buffer_size=1,
        )
        callback = client.callback
        assert callable(callback)
        await callback(SimpleNamespace(message=None))
        with pytest.raises(TelegramGatewayError):
            await anext(subscription)
        await subscription.close()
        with pytest.raises(StopAsyncIteration):
            await anext(subscription)

    run(scenario())
