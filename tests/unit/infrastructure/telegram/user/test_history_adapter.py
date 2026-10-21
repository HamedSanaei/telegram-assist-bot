from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest
from telethon.errors import FloodWaitError  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import (
    TelegramHistoryQuery,
    TelegramRateLimitError,
)
from telegram_assist_bot.infrastructure.telegram.user import TelethonHistoryAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


def raw(message_id: int) -> object:
    return SimpleNamespace(
        id=message_id,
        date=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        message=f"synthetic-{message_id}",
        entities=[],
        media=None,
        action=None,
    )


@dataclass
class Client:
    messages: list[object]
    calls: list[tuple[int, int, datetime]] = field(default_factory=list)

    async def _iterate(self) -> AsyncIterator[object]:
        for item in sorted(
            self.messages,
            key=lambda value: cast("SimpleNamespace", value).id,
            reverse=True,
        ):
            yield item

    def iter_messages(
        self,
        entity: int,
        *,
        limit: int,
        offset_date: datetime,
        offset_id: int = 0,
    ) -> AsyncIterator[object]:
        self.calls.append((entity, limit, offset_date))
        del offset_id
        return self._iterate()


def query() -> TelegramHistoryQuery:
    return TelegramHistoryQuery(
        -1001,
        datetime(2099, 3, 19, 20, 30, tzinfo=UTC),
        datetime(2099, 3, 20, 8, 0, tzinfo=UTC),
        2,
        3,
    )


def test_pages_sdk_iterator_without_exposing_cursor_tokens() -> None:
    async def scenario() -> None:
        client = Client([raw(index) for index in range(1, 6)])
        adapter = TelethonHistoryAdapter(client, "source", "منبع", 1)

        pages = [page async for page in adapter.iter_history_pages(query())]

        assert [len(page.messages) for page in pages] == [2, 2, 1]
        assert [item.source_message_id for page in pages for item in page.messages] == [
            5,
            4,
            3,
            2,
            1,
        ]
        assert client.calls == [(-1001, 6, query().end_exclusive)]

    run(scenario())


def test_empty_history_yields_no_pages() -> None:
    async def scenario() -> None:
        adapter = TelethonHistoryAdapter(Client([]), None, "Source", 1)
        assert [page async for page in adapter.iter_history_pages(query())] == []

    run(scenario())


def test_each_sdk_next_operation_has_bounded_timeout() -> None:
    @dataclass
    class BlockingClient(Client):
        async def _iterate(self) -> AsyncIterator[object]:
            await asyncio.Event().wait()
            yield raw(1)

    async def scenario() -> None:
        adapter = TelethonHistoryAdapter(BlockingClient([]), None, "Source", 0.001)
        with pytest.raises(TimeoutError):
            await anext(adapter.iter_history_pages(query()))

    run(scenario())


def test_streams_one_page_before_reading_later_history() -> None:
    @dataclass
    class CountingClient(Client):
        consumed: int = 0
        closed: bool = False

        async def _iterate(self) -> AsyncIterator[object]:
            try:
                for identity in (6, 5, 4, 3, 2, 1):
                    self.consumed += 1
                    yield raw(identity)
            finally:
                self.closed = True

    async def scenario() -> None:
        client = CountingClient([])
        pages = TelethonHistoryAdapter(client, None, "Source", 1).iter_history_pages(
            query()
        )
        first = await anext(pages)
        assert first.next_offset_message_id == 5
        assert first.newest_first
        assert client.consumed == 2
        await pages.aclose()
        assert client.closed

    run(scenario())


def test_malformed_individual_message_does_not_discard_other_page_members() -> None:
    async def scenario() -> None:
        malformed = SimpleNamespace(
            id=2, date=None, message="private malformed payload"
        )
        adapter = TelethonHistoryAdapter(Client([malformed, raw(1)]), None, "Source", 1)
        pages = [page async for page in adapter.iter_history_pages(query())]
        assert len(pages) == 1
        assert [item.source_message_id for item in pages[0].messages] == [1]
        assert pages[0].next_offset_message_id == 1
        assert [failure.source_message_id for failure in pages[0].failures] == [2]

    run(scenario())


def test_adapter_preserves_floodwait_without_clipping() -> None:
    class FloodClient(Client):
        async def _iterate(self) -> AsyncIterator[object]:
            yield raw(3)
            raise FloodWaitError(None, capture=7200)

    async def scenario() -> None:
        adapter = TelethonHistoryAdapter(FloodClient([]), None, "Source", 1)
        with pytest.raises(TelegramRateLimitError) as caught:
            await anext(adapter.iter_history_pages(query()))
        assert caught.value.retry_after_seconds == 7200

    run(scenario())
