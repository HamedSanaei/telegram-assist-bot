"""Prove bounded background recovery without unsafe logs or lost cancellation."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import ConfigurationError
from telegram_assist_bot.workers.background import supervise_background_task

if TYPE_CHECKING:
    from telegram_assist_bot.shared.observability import StructuredLogger


class Logger:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.suspended = asyncio.Event()

    def emit(self, **event: object) -> None:
        self.events.append(event)
        if event["event_name"] == "approval_background_task_suspended":
            self.suspended.set()


@pytest.mark.parametrize("failure", [RuntimeError, ConfigurationError])
def test_failures_are_bounded_and_suspend_until_cancelled(
    failure: type[Exception],
) -> None:
    async def scenario() -> None:
        logger = Logger()
        delays: list[float] = []
        calls = 0

        async def run() -> None:
            nonlocal calls
            calls += 1
            raise failure()

        async def sleep(delay: float) -> None:
            delays.append(delay)

        task = asyncio.create_task(
            supervise_background_task(
                run,
                task_name="synthetic",
                logger=cast("StructuredLogger", logger),
                retry_seconds=5,
                sleeper=sleep,
            )
        )
        await asyncio.wait_for(logger.suspended.wait(), timeout=1)
        assert calls == (1 if failure is ConfigurationError else 3)
        assert delays == ([] if failure is ConfigurationError else [5, 10])
        assert logger.events[-1]["level"] is LogLevel.CRITICAL
        assert not task.done()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_retry_recovers_and_caps_delay_without_echoing_error() -> None:
    async def scenario() -> None:
        logger = Logger()
        recovered = asyncio.Event()
        calls = 0
        delays: list[float] = []

        async def run() -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("synthetic-secret")
            recovered.set()
            await asyncio.Event().wait()

        async def sleep(delay: float) -> None:
            delays.append(delay)

        task = asyncio.create_task(
            supervise_background_task(
                run,
                task_name="synthetic",
                logger=cast("StructuredLogger", logger),
                retry_seconds=120,
                sleeper=sleep,
            )
        )
        await asyncio.wait_for(recovered.wait(), timeout=1)
        assert calls == 2
        assert delays == [60]
        assert "synthetic-secret" not in str(logger.events)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_clean_task_exit_is_detected_and_shutdown_interrupts_backoff() -> None:
    async def scenario() -> None:
        logger = Logger()
        sleeping = asyncio.Event()

        async def run() -> None:
            return None

        async def sleep(_delay: float) -> None:
            sleeping.set()
            await asyncio.Event().wait()

        task = asyncio.create_task(
            supervise_background_task(
                run,
                task_name="synthetic",
                logger=cast("StructuredLogger", logger),
                retry_seconds=5,
                sleeper=sleep,
            )
        )
        await asyncio.wait_for(sleeping.wait(), timeout=1)
        fields = cast("dict[str, object]", logger.events[0]["fields"])
        assert fields["failure_type"] == "BackgroundTaskStoppedError"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())


def test_invalid_recovery_bounds_fail_before_running() -> None:
    async def run() -> None:
        pytest.fail("Invalid policy must not run a task")

    with pytest.raises(ValueError, match="bounds"):
        asyncio.run(
            supervise_background_task(
                run,
                task_name="synthetic",
                logger=cast("StructuredLogger", Logger()),
                retry_seconds=0,
            )
        )
