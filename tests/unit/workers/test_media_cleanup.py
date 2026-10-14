"""Verify periodic media cleanup loop timing, draining and cancellation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.cleanup_expired_media import CleanupBatchResult
from telegram_assist_bot.shared.errors import ConfigurationError
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


@pytest.mark.parametrize("failures", [1, 2, 3])
def test_cleanup_cycle_recovery_is_bounded_and_observable(
    monkeypatch: pytest.MonkeyPatch, failures: int
) -> None:
    async def scenario() -> None:
        stop = asyncio.Event()
        calls = 0
        waits: list[float] = []
        events: list[tuple[str, int, float | None]] = []

        class FailingCleanup:
            async def execute(self, *, now: datetime) -> CleanupBatchResult:
                nonlocal calls
                del now
                calls += 1
                if calls <= failures:
                    raise RuntimeError("synthetic private error")
                stop.set()
                return CleanupBatchResult(deleted=1)

        async def wait_for(waiter: object, **options: float) -> bool:
            cast("Coroutine[object, object, bool]", waiter).close()
            timeout = options["timeout"]
            waits.append(timeout)
            if timeout < 60:
                raise TimeoutError
            return True

        monkeypatch.setattr(asyncio, "wait_for", wait_for)
        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", FailingCleanup()),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=60,
            max_batches_per_cycle=10,
            on_cycle_failed=lambda error, attempt, delay: events.append(
                (type(error).__name__, attempt, delay)
            ),
        )
        if failures == 3:
            with pytest.raises(RuntimeError, match="synthetic private"):
                await worker.run(stop)
        else:
            assert await worker.run(stop) == 1
        assert calls == min(failures + 1, 3)
        assert waits[:failures] == [5.0, 10.0][:failures]
        assert events == [
            ("RuntimeError", i + 1, delay)
            for i, delay in enumerate([5.0, 10.0, None][:failures])
        ]

    asyncio.run(scenario())


def test_cleanup_shutdown_interrupts_retry_delay() -> None:
    async def scenario() -> None:
        stop = asyncio.Event()

        class FailingCleanup:
            async def execute(self, *, now: datetime) -> CleanupBatchResult:
                del now
                raise RuntimeError

        def observe(_error: BaseException, _attempt: int, _delay: float | None) -> None:
            stop.set()

        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", FailingCleanup()),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=60,
            max_batches_per_cycle=10,
            on_cycle_failed=observe,
        )
        assert await worker.run(stop) == 0

    asyncio.run(scenario())


def test_cleanup_declared_permanent_failure_is_not_retried() -> None:
    async def scenario() -> None:
        calls = 0
        failures: list[float | None] = []

        class InvalidCleanup:
            async def execute(self, *, now: datetime) -> CleanupBatchResult:
                nonlocal calls
                del now
                calls += 1
                raise ConfigurationError

        worker = PeriodicMediaCleanupWorker(
            cast("CleanupExpiredMedia", InvalidCleanup()),
            cast("Clock", FixedClock(datetime(2026, 7, 27, tzinfo=UTC))),
            interval_seconds=60,
            max_batches_per_cycle=10,
            on_cycle_failed=lambda _error, _attempt, delay: failures.append(delay),
        )
        with pytest.raises(ConfigurationError):
            await worker.run(asyncio.Event())
        assert calls == 1
        assert failures == [None]

    asyncio.run(scenario())
