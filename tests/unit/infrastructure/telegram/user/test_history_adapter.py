from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ports import TelegramHistoryQuery
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
        for item in self.messages:
            yield item

    def iter_messages(
        self,
        entity: int,
        *,
        limit: int,
        offset_date: datetime,
    ) -> AsyncIterator[object]:
        self.calls.append((entity, limit, offset_date))
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
            1,
            2,
            3,
            4,
            5,
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
