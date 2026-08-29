"""One-shot and periodic triggers for bounded media cleanup."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from telegram_assist_bot.application.cleanup_expired_media import (
        CleanupBatchResult,
        CleanupExpiredMedia,
    )
    from telegram_assist_bot.application.ports import Clock
    from telegram_assist_bot.shared.retry.executor import AsyncSleeper

type BatchObserver = Callable[[CleanupBatchResult], Awaitable[None]]


async def cleanup_media_once(
    use_case: CleanupExpiredMedia, *, now: datetime
) -> CleanupBatchResult:
    """Run one bounded cleanup batch."""
    return await use_case.execute(now=now)


class PeriodicMediaCleanupWorker:
    """Drain bounded cleanup batches until shutdown is requested."""

    def __init__(
        self,
        use_case: CleanupExpiredMedia,
        clock: Clock,
        *,
        interval_seconds: int,
        max_batches_per_cycle: int,
        on_batch_completed: BatchObserver | None = None,
        batch_yield: AsyncSleeper = asyncio.sleep,
    ) -> None:
        """Initialize a validated periodic wake-up and drain policy."""
        if type(interval_seconds) is not int or not 60 <= interval_seconds <= 604_800:
            raise ValueError("Media cleanup interval is outside supported bounds.")
        if (
            type(max_batches_per_cycle) is not int
            or not 1 <= max_batches_per_cycle <= 1000
        ):
            raise ValueError("Media cleanup batch limit is outside supported bounds.")
        self._use_case = use_case
        self._clock = clock
        self._interval = float(interval_seconds)
        self._max_batches = max_batches_per_cycle
        self._on_batch_completed = on_batch_completed
        self._batch_yield = batch_yield

    async def run(self, stop_event: asyncio.Event) -> int:
        """Drain bounded batches per cycle with cancellation-safe sleep."""
        iterations = 0
        while not stop_event.is_set():
            await self._drain_cycle(stop_event)
            iterations += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                continue
        return iterations

    async def _drain_cycle(self, stop_event: asyncio.Event) -> int:
        """Process one wake-up: up to the safety bound of bounded batches."""
        batches = 0
        while not stop_event.is_set() and batches < self._max_batches:
            result = await self._use_case.execute(now=self._clock.utc_now())
            batches += 1
            if self._on_batch_completed is not None:
                await self._on_batch_completed(result)
            if not result.more_eligible_work:
                break
            await self._batch_yield(0)
        return batches


__all__ = ("PeriodicMediaCleanupWorker", "cleanup_media_once")
