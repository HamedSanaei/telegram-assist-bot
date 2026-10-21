"""Classify provider failures without retrying permanent history or leaking streams."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from telethon.errors import (  # type: ignore[import-untyped]
    ChannelPrivateError,
    RPCError,
    ServerError,
    UnauthorizedError,
)
from tests.unit.infrastructure.telegram.user.test_history_adapter import raw

from telegram_assist_bot.application.ports import (
    TelegramChannelPermissionError,
    TelegramHistoryQuery,
    TelegramMessageMappingError,
    TelegramSessionInvalidError,
    TelegramTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user.history_adapter import (
    TelethonHistoryAdapter,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (ConnectionError("synthetic"), TelegramTransientError),
        (TimeoutError(), TimeoutError),
        (ServerError(None, "synthetic", 500), TelegramTransientError),
        (ChannelPrivateError(None), TelegramChannelPermissionError),
        (UnauthorizedError(None, "synthetic", 401), TelegramSessionInvalidError),
        (RPCError(None, "synthetic", 400), RPCError),
    ],
)
def test_provider_failure_discards_only_unacknowledged_page_and_closes_iterator(
    failure: Exception,
    expected: type[Exception],
) -> None:
    class Client:
        closed = False

        def iter_messages(
            self,
            entity: int,
            *,
            limit: int,
            offset_date: datetime,
            offset_id: int = 0,
        ) -> AsyncIterator[object]:
            del entity, limit, offset_date, offset_id

            async def stream() -> AsyncIterator[object]:
                try:
                    yield raw(3)
                    raise failure
                finally:
                    self.closed = True

            return stream()

    async def scenario() -> None:
        client = Client()
        adapter = TelethonHistoryAdapter(client, None, "Synthetic source", 1)
        query = TelegramHistoryQuery(
            -100,
            datetime(2099, 3, 20, tzinfo=UTC),
            datetime(2099, 3, 20, 12, tzinfo=UTC),
            2,
            2,
        )
        with pytest.raises(expected):
            await anext(adapter.iter_history_pages(query))
        assert client.closed

    asyncio.run(scenario())


@pytest.mark.parametrize("identifiers", [(0, 1), (1, 2)])
def test_unidentified_or_non_descending_history_cannot_advance_a_cursor(
    identifiers: tuple[int, int],
) -> None:
    class Client:
        def iter_messages(
            self,
            entity: int,
            *,
            limit: int,
            offset_date: datetime,
            offset_id: int = 0,
        ) -> AsyncIterator[object]:
            del entity, limit, offset_date, offset_id

            async def stream() -> AsyncIterator[object]:
                for identifier in identifiers:
                    yield raw(identifier)

            return stream()

    async def scenario() -> None:
        adapter = TelethonHistoryAdapter(Client(), None, "Synthetic source", 1)
        query = TelegramHistoryQuery(
            -100,
            datetime(2099, 3, 20, tzinfo=UTC),
            datetime(2099, 3, 20, 12, tzinfo=UTC),
            2,
            2,
        )
        with pytest.raises(TelegramMessageMappingError) as caught:
            await anext(adapter.iter_history_pages(query))
        assert caught.value.source_message_id is None

    asyncio.run(scenario())
