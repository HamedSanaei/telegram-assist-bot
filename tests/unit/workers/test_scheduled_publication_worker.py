"""Verify bounded polling and cancellation propagation for schedule workers."""

from __future__ import annotations

import asyncio

import pytest

from telegram_assist_bot.workers import ScheduledPublicationWorker


@pytest.mark.parametrize("seconds", [0, -1, 301])
def test_rejects_unbounded_poll_intervals(seconds: float) -> None:
    async def run_once() -> None:
        return None

    with pytest.raises(ValueError, match="poll interval"):
        ScheduledPublicationWorker(run_once, poll_seconds=seconds)


def test_runs_iterations_and_propagates_sleeper_cancellation() -> None:
    calls: list[str] = []

    async def run_once() -> None:
        calls.append("run")

    async def cancel_after_delay(delay: float) -> None:
        assert delay == 2
        calls.append("sleep")
        raise asyncio.CancelledError

    worker = ScheduledPublicationWorker(
        run_once, poll_seconds=2, sleeper=cancel_after_delay
    )
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(worker.run())
    assert calls == ["run", "sleep"]
