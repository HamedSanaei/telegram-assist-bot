"""Telethon live-event adapter with bounded backpressure and clean unsubscribe."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Protocol

from telethon import events  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import (
    TelegramGatewayError,
    TelegramLiveSubscription,
    TelegramMessageMappingError,
    TelegramTextMessage,
)
from telegram_assist_bot.infrastructure.telegram.user.message_mapper import (
    InvalidTelegramMessageError,
    map_telethon_message,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

type EventCallback = Callable[[object], Awaitable[None]]


class TelethonEventClient(Protocol):
    """Describe handler registration without exposing it to Application."""

    def add_event_handler(self, callback: EventCallback, event: object) -> None:
        """Register one asynchronous SDK event callback."""
        ...

    def remove_event_handler(self, callback: EventCallback, event: object) -> int:
        """Remove the exact callback and event builder registration."""
        ...


_CLOSED: Final[object] = object()
type QueueItem = (
    TelegramTextMessage | TelegramMessageMappingError | TelegramGatewayError | object
)


@dataclass(slots=True)
class TelethonLiveSubscriptionImpl(TelegramLiveSubscription):
    """Consume one adapter queue and own its registered SDK callback."""

    _client: TelethonEventClient = field(repr=False)
    _queue: asyncio.Queue[QueueItem] = field(repr=False)
    _callback: EventCallback = field(repr=False)
    _event_builder: object = field(repr=False)
    _callback_tasks: set[asyncio.Task[object]] = field(repr=False)
    _closed: bool = False

    def __aiter__(self) -> TelethonLiveSubscriptionImpl:
        """Return this single-consumer subscription iterator."""
        return self

    async def __anext__(self) -> TelegramTextMessage:
        """Return one mapped event or propagate one safe adapter failure."""
        item = await self._queue.get()
        if item is _CLOSED:
            raise StopAsyncIteration
        if isinstance(item, TelegramGatewayError):
            raise item
        if isinstance(item, TelegramMessageMappingError):
            raise item
        if type(item) is not TelegramTextMessage:
            raise TelegramGatewayError
        return item

    async def close(self) -> None:
        """Unregister once, cancel blocked callbacks, and terminate the iterator."""
        if self._closed:
            return
        self._closed = True
        self._client.remove_event_handler(self._callback, self._event_builder)
        current = asyncio.current_task()
        pending = [task for task in self._callback_tasks if task is not current]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(_CLOSED)


@dataclass(frozen=True, slots=True)
class TelethonLiveAdapter:
    """Create one source-filtered bounded Telethon NewMessage subscription."""

    client: TelethonEventClient = field(repr=False)
    source_channel_username: str | None
    source_channel_display_name: str

    async def subscribe(
        self,
        source_channel_id: int,
        *,
        buffer_size: int,
    ) -> TelegramLiveSubscription:
        """Register a DTO-only callback whose queue applies direct backpressure."""
        if type(buffer_size) is not int or not 1 <= buffer_size <= 10_000:
            raise ValueError("buffer_size must be between 1 and 10000")
        queue: asyncio.Queue[QueueItem] = asyncio.Queue(maxsize=buffer_size)
        callback_tasks: set[asyncio.Task[object]] = set()

        async def callback(event: object) -> None:
            current = asyncio.current_task()
            if current is not None:
                callback_tasks.add(current)
            try:
                raw_message = getattr(event, "message", None)
                if raw_message is None:
                    await queue.put(TelegramMessageMappingError(None))
                    return
                try:
                    message = map_telethon_message(
                        raw_message,
                        source_channel_id=source_channel_id,
                        source_channel_username=self.source_channel_username,
                        source_channel_display_name=self.source_channel_display_name,
                    )
                except asyncio.CancelledError:
                    raise
                except (InvalidTelegramMessageError, TypeError, ValueError) as error:
                    await queue.put(
                        TelegramMessageMappingError(
                            getattr(raw_message, "id", None),
                            cause=error,
                        )
                    )
                    return
                await queue.put(message)
            finally:
                if current is not None:
                    callback_tasks.discard(current)

        event_builder = events.NewMessage(chats=source_channel_id)
        self.client.add_event_handler(callback, event_builder)
        return TelethonLiveSubscriptionImpl(
            self.client,
            queue,
            callback,
            event_builder,
            callback_tasks,
        )


__all__ = (
    "TelethonEventClient",
    "TelethonLiveAdapter",
    "TelethonLiveSubscriptionImpl",
)
