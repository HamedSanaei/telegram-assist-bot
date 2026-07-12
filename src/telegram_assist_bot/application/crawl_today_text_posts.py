"""Crawl one canonical source's current local-day text history."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, time
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    TextMessageIngestor,
)
from telegram_assist_bot.application.ports import (
    Clock,
    TelegramHistoryGateway,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramTextMessage,
)
from telegram_assist_bot.shared.retry import RetryPolicy, execute_with_retry

if TYPE_CHECKING:
    from zoneinfo import ZoneInfo

    from telegram_assist_bot.shared.retry.executor import (
        AsyncSleeper,
        JitterSource,
        RetryEventLogger,
    )


class HistoryPaginationLimitError(RuntimeError):
    """Report an adapter stream that exceeds its application bound."""

    error_category = "permanent"


@dataclass(frozen=True, slots=True)
class CrawlTodayResult:
    """Report payload-free counts from one bounded crawl invocation."""

    created: int = 0
    already_existing: int = 0
    skipped_service: int = 0
    skipped_empty: int = 0
    skipped_media_only: int = 0
    skipped_outside_interval: int = 0
    skipped_other_source: int = 0
    failed: int = 0


def current_local_day_interval(
    now_utc: datetime,
    timezone: ZoneInfo,
) -> tuple[datetime, datetime]:
    """Return today's local midnight inclusive through ``now_utc`` exclusive."""
    if now_utc.tzinfo is None or now_utc.utcoffset() is None:
        raise ValueError("now_utc must be timezone-aware")
    canonical_now = now_utc.astimezone(UTC)
    local_now = canonical_now.astimezone(timezone)
    local_start = datetime.combine(local_now.date(), time.min, tzinfo=timezone)
    return local_start.astimezone(UTC), canonical_now


@dataclass(frozen=True, slots=True)
class CrawlTodayTextPosts:
    """Fetch bounded history and persist exact source text idempotently."""

    gateway: TelegramHistoryGateway = field(repr=False)
    ingestor: TextMessageIngestor = field(repr=False)
    clock: Clock = field(repr=False)
    timezone: ZoneInfo
    retry_policy: RetryPolicy
    logger: RetryEventLogger = field(repr=False)
    sleeper: AsyncSleeper = field(repr=False)
    jitter_source: JitterSource = field(repr=False)
    page_size: int = 100
    max_pages: int = 100

    async def execute(
        self,
        source_channel_id: int,
        *,
        correlation_id: str,
    ) -> CrawlTodayResult:
        """Crawl exactly one canonical source over ``[local midnight, now)``."""
        if type(source_channel_id) is not int or source_channel_id == 0:
            raise ValueError("source_channel_id must be a non-zero integer")
        if type(self.page_size) is not int or not 1 <= self.page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= 1000:
            raise ValueError("max_pages must be between 1 and 1000")
        received_at = self.clock.utc_now()
        start, end = current_local_day_interval(received_at, self.timezone)
        query = TelegramHistoryQuery(
            source_channel_id=source_channel_id,
            start_inclusive=start,
            end_exclusive=end,
            page_size=self.page_size,
            max_pages=self.max_pages,
        )

        async def collect() -> tuple[TelegramTextMessage, ...]:
            messages: list[TelegramTextMessage] = []
            page_count = 0
            async for page in self.gateway.iter_history_pages(query):
                page_count += 1
                if page_count > self.max_pages:
                    raise HistoryPaginationLimitError
                messages.extend(self._page_messages(page))
            return tuple(messages)

        messages = await execute_with_retry(
            collect,
            operation_name="telegram_history_today",
            operation_is_safe_to_retry=True,
            policy=self.retry_policy,
            logger=self.logger,
            sleeper=self.sleeper,
            jitter_source=self.jitter_source,
        )
        counts = _MutableCounts()
        for message in messages:
            skip = self._skip_reason(message, query)
            if skip is not None:
                counts.increment(skip)
                continue
            result = await self.ingestor.execute(
                message,
                correlation_id=correlation_id,
            )
            if result.outcome is IngestionOutcome.CREATED:
                counts.created += 1
            elif result.outcome is IngestionOutcome.ALREADY_EXISTS:
                counts.already_existing += 1
            else:
                counts.failed += 1
        return counts.freeze()

    @staticmethod
    def _page_messages(page: TelegramHistoryPage) -> tuple[TelegramTextMessage, ...]:
        if type(page) is not TelegramHistoryPage:
            raise TypeError("history gateway must yield TelegramHistoryPage")
        return page.messages

    @staticmethod
    def _skip_reason(
        message: TelegramTextMessage,
        query: TelegramHistoryQuery,
    ) -> str | None:
        if message.source_channel_id != query.source_channel_id:
            return "skipped_other_source"
        if not (
            query.start_inclusive <= message.source_published_at < query.end_exclusive
        ):
            return "skipped_outside_interval"
        if message.is_service:
            return "skipped_service"
        has_text = message.text is not None and message.text != ""
        has_caption = message.caption is not None and message.caption != ""
        if not has_text and not has_caption and not message.media:
            return "skipped_media_only" if message.has_media else "skipped_empty"
        return None


@dataclass(slots=True)
class _MutableCounts:
    created: int = 0
    already_existing: int = 0
    skipped_service: int = 0
    skipped_empty: int = 0
    skipped_media_only: int = 0
    skipped_outside_interval: int = 0
    skipped_other_source: int = 0
    failed: int = 0

    def increment(self, field_name: str) -> None:
        """Increment one known skip counter without accepting arbitrary fields."""
        if field_name == "skipped_service":
            self.skipped_service += 1
        elif field_name == "skipped_empty":
            self.skipped_empty += 1
        elif field_name == "skipped_media_only":
            self.skipped_media_only += 1
        elif field_name == "skipped_outside_interval":
            self.skipped_outside_interval += 1
        elif field_name == "skipped_other_source":
            self.skipped_other_source += 1
        else:
            raise ValueError("unknown crawl result field")

    def freeze(self) -> CrawlTodayResult:
        """Return an immutable public result snapshot."""
        return CrawlTodayResult(
            created=self.created,
            already_existing=self.already_existing,
            skipped_service=self.skipped_service,
            skipped_empty=self.skipped_empty,
            skipped_media_only=self.skipped_media_only,
            skipped_outside_interval=self.skipped_outside_interval,
            skipped_other_source=self.skipped_other_source,
            failed=self.failed,
        )


__all__ = (
    "CrawlTodayResult",
    "CrawlTodayTextPosts",
    "HistoryPaginationLimitError",
    "current_local_day_interval",
)
