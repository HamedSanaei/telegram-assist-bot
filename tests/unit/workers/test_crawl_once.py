"""Thin crawl worker delegation tests."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

from telegram_assist_bot.workers.crawl_once import CrawlOnceWorker

if TYPE_CHECKING:
    from telegram_assist_bot.application import CrawlTodayResult, CrawlTodayTextPosts


class Crawler:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[int, str]] = []

    async def execute(
        self,
        source_channel_id: int,
        *,
        correlation_id: str,
    ) -> object:
        self.calls.append((source_channel_id, correlation_id))
        return self.result


def test_crawl_once_delegates_canonical_source_and_correlation() -> None:
    expected = object()
    crawler = Crawler(expected)
    worker = CrawlOnceWorker(cast("CrawlTodayTextPosts", crawler))

    result = asyncio.run(worker.run(-100123, correlation_id="corr-1"))

    assert result is cast("CrawlTodayResult", expected)
    assert crawler.calls == [(-100123, "corr-1")]
