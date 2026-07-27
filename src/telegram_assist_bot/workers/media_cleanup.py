"""One-shot and periodic triggers for bounded media cleanup."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.cleanup_expired_media import (
        CleanupExpiredMedia,
    )
    from telegram_assist_bot.application.ports import Clock


async def cleanup_media_once(use_case: CleanupExpiredMedia, *, now: datetime) -> int:
    """Run one bounded cleanup batch."""
    return await use_case.execute(now=now)


class PeriodicMediaCleanupWorker:
    """Run bounded MongoDB-backed cleanup batches until shutdown is requested."""

    def __init__(
        self,
        use_case: CleanupExpiredMedia,
        clock: Clock,
        *,
        interval_seconds: int,
    ) -> None:
        """Initialize a validated periodic wake-up policy."""
        if type(interval_seconds) is not int or not 60 <= interval_seconds <= 604_800:
            raise ValueError("Media cleanup interval is outside supported bounds.")
        self._use_case = use_case
        self._clock = clock
        self._interval = float(interval_seconds)

    async def run(self, stop_event: asyncio.Event) -> int:
        """Run one bounded batch per iteration with cancellation-safe sleep."""
        iterations = 0
        while not stop_event.is_set():
            await self._use_case.execute(now=self._clock.utc_now())
            iterations += 1
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=self._interval)
            except TimeoutError:
                continue
        return iterations


__all__ = ("PeriodicMediaCleanupWorker", "cleanup_media_once")
