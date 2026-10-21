"""Bounded newest-first Telethon pages with stable exclusive message-ID cursors."""

from __future__ import annotations

import asyncio
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from telethon.errors import (  # type: ignore[import-untyped]
    ChannelPrivateError,
    FloodWaitError,
    RPCError,
    ServerError,
    UnauthorizedError,
)

from telegram_assist_bot.application.ports import (
    TelegramChannelPermissionError,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramMessageMappingError,
    TelegramRateLimitError,
    TelegramSessionInvalidError,
    TelegramTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user.message_mapper import (
    map_telethon_message,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, AsyncIterator
    from datetime import datetime


class TelethonHistoryClient(Protocol):
    """Describe Telethon's descending, exclusive-offset history iterator."""

    def iter_messages(
        self,
        entity: int,
        *,
        limit: int,
        offset_date: datetime,
        offset_id: int = 0,
    ) -> AsyncIterator[object]:
        """Return newest-first history strictly older than the offset ID."""
        ...


@dataclass(frozen=True, slots=True)
class TelethonHistoryAdapter:
    """Retain no more than one bounded page and isolate malformed messages."""

    client: TelethonHistoryClient = field(repr=False)
    source_channel_username: str | None
    source_channel_display_name: str
    timeout_seconds: float

    async def iter_history_pages(
        self, query: TelegramHistoryQuery
    ) -> AsyncGenerator[TelegramHistoryPage, None]:
        """Expose an acknowledged-page cursor; incomplete pages are retried alone."""
        maximum_messages = query.page_size * query.max_pages
        iterator = self.client.iter_messages(
            query.source_channel_id,
            limit=maximum_messages,
            offset_date=query.end_exclusive,
            offset_id=query.offset_message_id,
        )
        page: list[object] = []
        try:
            for _ in range(maximum_messages):
                try:
                    async with asyncio.timeout(self.timeout_seconds):
                        raw = await anext(iterator)
                except StopAsyncIteration:
                    break
                page.append(raw)
                if len(page) == query.page_size:
                    mapped = self._map_page(page, query)
                    yield mapped
                    page = []
                    if any(
                        message.source_published_at < query.start_inclusive
                        for message in mapped.messages
                    ):
                        return
            if page:
                yield self._map_page(page, query)
        except asyncio.CancelledError:
            raise
        except FloodWaitError as error:
            raise TelegramRateLimitError(max(0, int(error.seconds))) from error
        except ChannelPrivateError as error:
            raise TelegramChannelPermissionError from error
        except UnauthorizedError as error:
            raise TelegramSessionInvalidError from error
        except TimeoutError:
            raise
        except (ConnectionError, OSError, ServerError) as error:
            raise TelegramTransientError from error
        except RPCError:
            # Unknown RPC failures are not assumed transient.
            raise
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()

    def _map_page(
        self, raw_messages: list[object], query: TelegramHistoryQuery
    ) -> TelegramHistoryPage:
        messages = []
        failures = []
        ids = []
        for raw in raw_messages:
            identity = getattr(raw, "id", None)
            if type(identity) is not int or identity <= 0:
                # An unidentified item cannot be safely acknowledged by a cursor.
                raise TelegramMessageMappingError(None)
            ids.append(identity)
            try:
                messages.append(
                    map_telethon_message(
                        raw,
                        source_channel_id=query.source_channel_id,
                        source_channel_username=self.source_channel_username,
                        source_channel_display_name=self.source_channel_display_name,
                    )
                )
            except Exception as error:  # noqa: BLE001 - one malformed source item.
                failures.append(TelegramMessageMappingError(identity, cause=error))
        # Telethon guarantees descending IDs; refuse malformed pagination instead
        # of silently skipping messages or applying an unsafe timestamp cutoff.
        if any(left <= right for left, right in itertools.pairwise(ids)):
            raise TelegramMessageMappingError(None)
        return TelegramHistoryPage(tuple(messages), min(ids), True, tuple(failures))


__all__ = ("TelethonHistoryAdapter", "TelethonHistoryClient")
