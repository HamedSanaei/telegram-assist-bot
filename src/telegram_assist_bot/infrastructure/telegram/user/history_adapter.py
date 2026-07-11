"""Bounded Telethon history paging with SDK-local cursor management."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.application.ports import (
    TelegramHistoryPage,
    TelegramHistoryQuery,
)
from telegram_assist_bot.infrastructure.telegram.user.message_mapper import (
    map_telethon_message,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime


class TelethonHistoryClient(Protocol):
    """Describe the SDK iterator surface consumed by the history adapter."""

    def iter_messages(
        self,
        entity: int,
        *,
        limit: int,
        offset_date: datetime,
    ) -> AsyncIterator[object]:
        """Return an SDK-owned paginated history iterator."""
        ...


@dataclass(frozen=True, slots=True)
class TelethonHistoryAdapter:
    """Map a bounded SDK history iterator into token-free application pages."""

    client: TelethonHistoryClient = field(repr=False)
    source_channel_username: str | None
    source_channel_display_name: str
    timeout_seconds: float

    async def iter_history_pages(
        self,
        query: TelegramHistoryQuery,
    ) -> AsyncIterator[TelegramHistoryPage]:
        """Yield at most ``max_pages`` pages while preserving source payloads."""
        maximum_messages = query.page_size * query.max_pages
        iterator = self.client.iter_messages(
            query.source_channel_id,
            limit=maximum_messages,
            offset_date=query.end_exclusive,
        )
        page: list[object] = []
        for _ in range(maximum_messages):
            try:
                async with asyncio.timeout(self.timeout_seconds):
                    raw = await anext(iterator)
            except StopAsyncIteration:
                break
            page.append(raw)
            if len(page) == query.page_size:
                yield self._map_page(page, query)
                page = []
        if page:
            yield self._map_page(page, query)

    def _map_page(
        self,
        raw_messages: list[object],
        query: TelegramHistoryQuery,
    ) -> TelegramHistoryPage:
        return TelegramHistoryPage(
            tuple(
                map_telethon_message(
                    raw,
                    source_channel_id=query.source_channel_id,
                    source_channel_username=self.source_channel_username,
                    source_channel_display_name=self.source_channel_display_name,
                )
                for raw in raw_messages
            )
        )


__all__ = ("TelethonHistoryAdapter", "TelethonHistoryClient")
