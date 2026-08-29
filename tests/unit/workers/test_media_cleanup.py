"""Verify periodic media cleanup loop timing, draining and cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.cleanup_expired_media import CleanupBatchResult
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

    async def execute(self, *, now: datetime) -> CleanupBatchResult:
        self.calls.append(now)
        self.started.set()
        return CleanupBatchResult()


class DrainingCleanup:
    """Return bounded results so one cycle drains exactly three batches."""

    def __init__(self) -> None:
        self.calls: list[datetime] = []
        self.started = asyncio.Event()
        self.results = (
            CleanupBatchResult(scanned=100, deleted=100, more_eligible_work=True),
            CleanupBatchResult(scanned=100, deleted=100, more_eligible_work=True),
            CleanupBatchResult(scanned=50, deleted=50),
        )

    async def execute(self, *, now: datetime) -> CleanupBatchResult:
        self.calls.append(now)
        self.started.set()
        return self.results[min(len(self.calls) - 1, len(self.results) - 1)]


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
            max_batches_per_cycle=10,
        )
        task = asyncio.create_task(worker.run(stop))
        await cleanup.started.wait()
        assert await task == 2
        assert waits == [60.0, 60.0]

    asyncio.run(scenario())
    assert cleanup.calls == [now, now]


def test_worker_drains_multiple_batches_per_cycle_without_intervals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One wake-up processes several batches before sleeping once."""
    now = datetime(2026, 7, 27, tzinfo=UTC)
    cleanup = DrainingCleanup()
    stop = asyncio.Event()

    async def scenario() -> None:
        waits: list[float] = []

        async def wait_for(_waiter: object, **options: float) -> bool:
            cast("Coroutine[object, object, bool]", _waiter).close()
            waits.append(options["timeout"])
            stop.set()
            return True

        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", cleanup),
            cast("Clock", FixedClock(now)),
            interval_seconds=60,
            max_batches_per_cycle=10,
        )
        task = asyncio.create_task(worker.run(stop))
        await cleanup.started.wait()
        assert await task == 1
        assert waits == [60.0]
        assert len(cleanup.calls) == 3

    asyncio.run(scenario())


def test_worker_respects_max_batches_per_cycle_bound(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    cleanup = DrainingCleanup()
    stop = asyncio.Event()

    async def scenario() -> None:
        waits: list[float] = []

        async def wait_for(_waiter: object, **options: float) -> bool:
            cast("Coroutine[object, object, bool]", _waiter).close()
            waits.append(options["timeout"])
            stop.set()
            return True

        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", cleanup),
            cast("Clock", FixedClock(now)),
            interval_seconds=60,
            max_batches_per_cycle=2,
        )
        task = asyncio.create_task(worker.run(stop))
        await cleanup.started.wait()
        assert await task == 1
        assert len(cleanup.calls) == 2

    asyncio.run(scenario())


def test_periodic_worker_cancellation_interrupts_sleep() -> None:
    cleanup = Cleanup()

    async def scenario() -> None:
        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", cleanup),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=60,
            max_batches_per_cycle=10,
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
            max_batches_per_cycle=10,
        )


@pytest.mark.parametrize("batches", [True, 0, 1001])
def test_periodic_worker_rejects_invalid_batch_limits(batches: object) -> None:
    with pytest.raises(
        ValueError,
        match="Media cleanup batch limit is outside supported bounds",
    ):
        PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", Cleanup()),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=60,
            max_batches_per_cycle=cast("int", batches),
        )


def test_cleanup_media_once_delegates_the_explicit_time() -> None:
    now = datetime(2026, 7, 27, tzinfo=UTC)
    cleanup = Cleanup()

    result = asyncio.run(
        cleanup_media_once(cast("CleanupExpiredMedia", cleanup), now=now)
    )

    assert result == CleanupBatchResult()
    assert cleanup.calls == [now]
