"""Thin one-shot worker trigger for a canonical source history crawl."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram_assist_bot.application import CrawlTodayResult, CrawlTodayTextPosts


@dataclass(frozen=True, slots=True)
class CrawlOnceWorker:
    """Invoke the application crawl use case without owning ingestion policy."""

    crawler: CrawlTodayTextPosts = field(repr=False)

    async def run(
        self,
        source_channel_id: int,
        *,
        correlation_id: str,
    ) -> CrawlTodayResult:
        """Run one bounded crawl for exactly one canonical source."""
        return await self.crawler.execute(
            source_channel_id,
            correlation_id=correlation_id,
        )


__all__ = ("CrawlOnceWorker",)
