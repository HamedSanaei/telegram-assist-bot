"""Stream crash-resumable current-local-day history with bounded operation retries."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, time
from time import perf_counter
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    IngestionResult,
    TextMessageIngestor,
)
from telegram_assist_bot.application.ports import (
    Clock,
    TelegramHistoryGateway,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramRateLimitError,
    TelegramTextMessage,
)
from telegram_assist_bot.application.ports.history_checkpoint import (
    HistoryCheckpoint,
    HistoryCheckpointRepository,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import ErrorCategory, classify_error
from telegram_assist_bot.shared.runtime_metrics import process_resources

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from zoneinfo import ZoneInfo

    from telegram_assist_bot.shared.retry import RetryPolicy
    from telegram_assist_bot.shared.retry.executor import (
        AsyncSleeper,
        JitterSource,
        RetryEventLogger,
    )


class HistoryPaginationLimitError(RuntimeError):
    """Report an adapter that cannot safely resume at its acknowledged cursor."""

    error_category = "permanent"


@dataclass(frozen=True, slots=True)
class CrawlTodayResult:
    """Expose aggregate work without source text or exception payloads."""

    created: int = 0
    already_existing: int = 0
    skipped_service: int = 0
    skipped_empty: int = 0
    skipped_media_only: int = 0
    skipped_outside_interval: int = 0
    skipped_other_source: int = 0
    failed: int = 0
    page_requests: int = 0
    pages_acknowledged: int = 0
    retries: int = 0
    duration_seconds: float = 0
    completed: bool = False


def current_local_day_interval(
    now_utc: datetime, timezone: ZoneInfo
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
    """Acknowledge complete pages, never a max-ID observed by a live producer."""

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
    checkpoints: HistoryCheckpointRepository | None = field(default=None, repr=False)
    maximum_flood_wait_seconds: float = 60
    database_operations: Callable[[], int] | None = field(default=None, repr=False)
    ingestion_metrics: Callable[[], dict[str, int]] | None = field(
        default=None, repr=False
    )

    async def execute(
        self, source_channel_id: int, *, correlation_id: str
    ) -> CrawlTodayResult:
        """Resume a fixed interrupted snapshot before opening a newer traversal."""
        if type(source_channel_id) is not int or source_channel_id == 0:
            raise ValueError("source_channel_id must be a non-zero integer")
        if type(self.page_size) is not int or not 1 <= self.page_size <= 1000:
            raise ValueError("page_size must be between 1 and 1000")
        if type(self.max_pages) is not int or not 1 <= self.max_pages <= 1000:
            raise ValueError("max_pages must be between 1 and 1000")
        counts = _MutableCounts()
        started = perf_counter()
        resources = process_resources()
        db_start = self.database_operations() if self.database_operations else 0
        media_start = self.ingestion_metrics() if self.ingestion_metrics else {}
        try:
            await self._crawl(source_channel_id, correlation_id, counts)
        finally:
            counts.duration_seconds = perf_counter() - started
            fields: dict[str, object] = asdict(counts)
            fields["source_channel_id"] = source_channel_id
            after = process_resources()
            fields.update(after)
            fields["cpu_seconds"] = after["cpu_seconds"] - resources["cpu_seconds"]
            if self.database_operations:
                fields["database_operations"] = self.database_operations() - db_start
            if self.ingestion_metrics:
                fields.update(
                    {
                        key: value - media_start.get(key, 0)
                        for key, value in self.ingestion_metrics().items()
                    }
                )
            self.logger.emit(
                level=LogLevel.INFO, event_name="history_crawl_summary", fields=fields
            )
        return CrawlTodayResult(**asdict(counts))

    async def _crawl(
        self,
        source_channel_id: int,
        correlation_id: str,
        counts: _MutableCounts,
        *,
        page_budget: int | None = None,
    ) -> None:
        start, end = current_local_day_interval(self.clock.utc_now(), self.timezone)
        old = (
            await self.checkpoints.get(source_channel_id) if self.checkpoints else None
        )
        budget = self.max_pages if page_budget is None else page_budget
        resuming = old is not None and not old.completed
        if old is not None and not old.completed:
            checkpoint = old
        else:
            checkpoint = HistoryCheckpoint(
                source_channel_id,
                start,
                end,
                revision=old.revision + 1 if old else 0,
                previous_high_message_id=(
                    old.high_message_id
                    if old and old.ordered and old.start_inclusive == start
                    else 0
                ),
            )
            if self.checkpoints and not await self.checkpoints.save(
                checkpoint, expected_revision=old.revision if old else None
            ):
                return
        await self._history_hold(source_channel_id, checkpoint.end_exclusive, held=True)
        query = TelegramHistoryQuery(
            source_channel_id,
            checkpoint.start_inclusive,
            checkpoint.end_exclusive,
            self.page_size,
            max(1, budget),
            checkpoint.offset_message_id,
        )
        iterator: AsyncIterator[TelegramHistoryPage] | None = None
        pages = 0

        async def next_page() -> TelegramHistoryPage | None:
            nonlocal iterator
            counts.page_requests += 1
            if iterator is None:
                iterator = self.gateway.iter_history_pages(query)
            try:
                return await anext(iterator)
            except StopAsyncIteration:
                return None
            except Exception:
                iterator = None
                if pages and not checkpoint.offset_message_id:
                    raise HistoryPaginationLimitError from None
                raise

        try:
            while pages < budget:
                page = await self._operation(next_page, "telegram_history_page", counts)
                if page is None:
                    if not await self._save(checkpoint, completed=True):
                        return
                    if resuming and checkpoint.end_exclusive < end:
                        await self._crawl(
                            source_channel_id,
                            correlation_id,
                            counts,
                            page_budget=budget - pages,
                        )
                    else:
                        await self._history_hold(
                            source_channel_id, checkpoint.end_exclusive, held=False
                        )
                        counts.completed = True
                    return
                if type(page) is not TelegramHistoryPage:
                    raise TypeError("history gateway must yield TelegramHistoryPage")
                pages += 1
                acknowledged = True
                for failure in page.failures:
                    counts.failed += 1
                    if failure.source_message_id is None:
                        acknowledged = False
                    else:
                        await self._record_failure(
                            source_channel_id, failure.source_message_id, "permanent"
                        )
                for message in page.messages:
                    skip = self._skip_reason(message, query)
                    if skip is not None:
                        setattr(counts, skip, getattr(counts, skip) + 1)
                        continue
                    if self.checkpoints and await self.checkpoints.is_terminal_failure(
                        source_channel_id, message.source_message_id
                    ):
                        counts.failed += 1
                        continue

                    async def ingest_message(
                        message: TelegramTextMessage = message,
                    ) -> IngestionResult:
                        return await self.ingestor.execute(
                            message, correlation_id=correlation_id
                        )

                    try:
                        result = await self._operation(
                            ingest_message,
                            "telegram_history_message",
                            counts,
                        )
                    except asyncio.CancelledError:
                        raise
                    except Exception as error:  # noqa: BLE001 - isolate individual messages.
                        counts.failed += 1
                        classification = classify_error(error)
                        if classification.category in {
                            ErrorCategory.PERMANENT,
                            ErrorCategory.VALIDATION,
                        }:
                            await self._record_failure(
                                source_channel_id,
                                message.source_message_id,
                                classification.category.value,
                            )
                        else:
                            acknowledged = False
                        continue
                    if result.outcome is IngestionOutcome.CREATED:
                        counts.created += 1
                    elif result.outcome is IngestionOutcome.ALREADY_EXISTS:
                        counts.already_existing += 1
                    else:
                        counts.failed += 1
                        await self._record_failure(
                            source_channel_id,
                            message.source_message_id,
                            ErrorCategory.CONCURRENCY_CONFLICT.value,
                        )
                if not acknowledged:
                    return
                source_messages = tuple(
                    message
                    for message in page.messages
                    if message.source_channel_id == source_channel_id
                )
                high = max(
                    (message.source_message_id for message in source_messages),
                    default=checkpoint.high_message_id,
                )
                high = max(
                    high,
                    max(
                        (failure.source_message_id or 0 for failure in page.failures),
                        default=0,
                    ),
                )
                # Only the adapter's explicit ordering guarantee permits early stopping.
                cutoff = (
                    checkpoint.ordered
                    and page.newest_first
                    and any(
                        message.source_published_at < query.start_inclusive
                        or (
                            checkpoint.previous_high_message_id > 0
                            and message.source_message_id
                            <= checkpoint.previous_high_message_id
                        )
                        for message in source_messages
                    )
                )
                next_offset = page.next_offset_message_id
                if next_offset is not None and (
                    next_offset <= 0
                    or (
                        checkpoint.offset_message_id
                        and next_offset >= checkpoint.offset_message_id
                    )
                ):
                    raise HistoryPaginationLimitError
                updated = replace(
                    checkpoint,
                    high_message_id=max(checkpoint.high_message_id, high),
                    offset_message_id=next_offset or checkpoint.offset_message_id,
                    ordered=checkpoint.ordered and page.newest_first,
                )
                if not await self._save(updated, completed=cutoff):
                    return
                counts.pages_acknowledged += 1
                checkpoint = replace(
                    updated, revision=updated.revision + 1, completed=cutoff
                )
                if cutoff:
                    if resuming and checkpoint.end_exclusive < end:
                        await self._crawl(
                            source_channel_id,
                            correlation_id,
                            counts,
                            page_budget=budget - pages,
                        )
                    else:
                        await self._history_hold(
                            source_channel_id, checkpoint.end_exclusive, held=False
                        )
                        counts.completed = True
                    return
                query = replace(query, offset_message_id=checkpoint.offset_message_id)
                if next_offset is None and self.checkpoints:
                    # A durable cursor is required before requesting another page.
                    raise HistoryPaginationLimitError
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                await close()

    async def _history_hold(
        self, source_channel_id: int, snapshot_end: datetime, *, held: bool
    ) -> None:
        hook = getattr(self.ingestor, "set_history_hold", None)
        if hook is not None:
            await hook(source_channel_id, held=held, snapshot_end=snapshot_end)

    async def _save(self, checkpoint: HistoryCheckpoint, *, completed: bool) -> bool:
        if self.checkpoints is None:
            return True
        return await self.checkpoints.save(
            replace(checkpoint, revision=checkpoint.revision + 1, completed=completed),
            expected_revision=checkpoint.revision,
        )

    async def _record_failure(
        self, channel_id: int, message_id: int, category: str
    ) -> None:
        if self.checkpoints:
            await self.checkpoints.record_terminal_failure(
                channel_id,
                message_id,
                category=category,
                recorded_at=self.clock.utc_now(),
            )
        self.logger.emit(
            level=LogLevel.ERROR,
            event_name="history_message_failed",
            fields={
                "source_channel_id": channel_id,
                "source_message_id": message_id,
                "failure_category": category,
            },
        )

    async def _operation[T](
        self, operation: Callable[[], Awaitable[T]], name: str, counts: _MutableCounts
    ) -> T:
        for attempt in range(1, self.retry_policy.max_attempts + 1):
            try:
                return await operation()
            except asyncio.CancelledError:
                raise
            except Exception as error:
                cancellation = _retained_cancellation(error)
                if cancellation is not None:
                    raise cancellation from None
                classification = classify_error(error)
                flood_wait = (
                    error.retry_after_seconds
                    if isinstance(error, TelegramRateLimitError)
                    else getattr(error, "retry_after_seconds", 0)
                )
                if (
                    not classification.retryable
                    or attempt == self.retry_policy.max_attempts
                    or flood_wait > self.maximum_flood_wait_seconds
                ):
                    self.logger.emit(
                        level=LogLevel.ERROR,
                        event_name="retry_exhausted",
                        fields={
                            "operation_name": name,
                            "attempt": attempt,
                            "failure_category": classification.category.value,
                        },
                    )
                    raise
                delay = max(
                    float(flood_wait),
                    self.retry_policy.delay_for_retry(
                        attempt, random_value=self.jitter_source()
                    ),
                )
                counts.retries += 1
                self.logger.emit(
                    level=LogLevel.WARNING,
                    event_name="retry_scheduled",
                    fields={
                        "operation_name": name,
                        "attempt": attempt,
                        "delay_seconds": delay,
                    },
                )
                await self.sleeper(delay)
        raise AssertionError("bounded retry policy has no attempts")

    @staticmethod
    def _skip_reason(
        message: TelegramTextMessage, query: TelegramHistoryQuery
    ) -> str | None:
        if message.source_channel_id != query.source_channel_id:
            return "skipped_other_source"
        if (
            not query.start_inclusive
            <= message.source_published_at
            < query.end_exclusive
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
    page_requests: int = 0
    pages_acknowledged: int = 0
    retries: int = 0
    duration_seconds: float = 0
    completed: bool = False


__all__ = (
    "CrawlTodayResult",
    "CrawlTodayTextPosts",
    "HistoryPaginationLimitError",
    "current_local_day_interval",
)


def _retained_cancellation(error: BaseException) -> asyncio.CancelledError | None:
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, asyncio.CancelledError):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None
