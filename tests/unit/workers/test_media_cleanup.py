"""Verify periodic media cleanup loop timing and cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.workers.media_cleanup import (
    PeriodicMediaCleanupWorker,
    cleanup_media_once,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from telegram_assist_bot.application.cleanup_expired_media import (
        CleanupExpiredMedia,
    )
    from telegram_assist_bot.application.ports import Clock


class FixedClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def utc_now(self) -> datetime:
        return self.value


class Cleanup:
    def __init__(self) -> None:
        self.calls: list[datetime] = []
        self.started = asyncio.Event()

    async def execute(self, *, now: datetime) -> int:
        self.calls.append(now)
        self.started.set()
        return 0


def test_periodic_worker_runs_bounded_iterations_and_stops_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    cleanup = Cleanup()
    stop = asyncio.Event()

    async def scenario() -> None:
        waits: list[float] = []

        async def wait_for(_waiter: object, **options: float) -> bool:
            cast("Coroutine[object, object, bool]", _waiter).close()
            timeout = options["timeout"]
            waits.append(timeout)
            if len(waits) == 1:
                raise TimeoutError
            stop.set()
            return True

        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", cleanup),
            cast("Clock", FixedClock(now)),
            interval_seconds=60,
        )
        task = asyncio.create_task(worker.run(stop))
        await cleanup.started.wait()
        assert await task == 2
        assert waits == [60.0, 60.0]

    asyncio.run(scenario())
    assert cleanup.calls == [now, now]


def test_periodic_worker_cancellation_interrupts_sleep() -> None:
    cleanup = Cleanup()

    async def scenario() -> None:
        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", cleanup),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=60,
        )
        task = asyncio.create_task(worker.run(asyncio.Event()))
        await cleanup.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


@pytest.mark.parametrize("interval", [True, 59, 604_801])
def test_periodic_worker_rejects_invalid_intervals(interval: object) -> None:
    with pytest.raises(
        ValueError,
        match="Media cleanup interval is outside supported bounds",
    ):
        PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", Cleanup()),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=cast("int", interval),
        )


def test_cleanup_media_once_delegates_the_explicit_time() -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    cleanup = Cleanup()

    result = asyncio.run(
        cleanup_media_once(cast("CleanupExpiredMedia", cleanup), now=now)
    )

    assert result == 0
    assert cleanup.calls == [now]
