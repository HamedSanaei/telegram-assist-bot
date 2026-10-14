"""Isolate approval background lifetimes with bounded observable recovery."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import (
    StartupFailureClass,
    classify_declared_failure,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.shared.observability import StructuredLogger
    from telegram_assist_bot.shared.retry import AsyncSleeper


class BackgroundTaskStoppedError(RuntimeError):
    """Report an unexpected clean exit from an infinite background lifetime."""


async def supervise_background_task(
    run: Callable[[], Awaitable[None]],
    *,
    task_name: str,
    logger: StructuredLogger,
    retry_seconds: float,
    max_attempts: int = 3,
    sleeper: AsyncSleeper = asyncio.sleep,
) -> None:
    """Retry a failed lifetime, then suspend it visibly without stopping polling.

    Every retry constructs a fresh coroutine over the same durable collaborators.
    Cancellation always propagates. A declared permanent failure or exhausted
    retry budget parks this task until shutdown and emits CRITICAL; its siblings
    and administrator polling remain available for diagnosis and recovery.
    """
    if retry_seconds <= 0 or not 1 <= max_attempts <= 10:
        raise ValueError("Background recovery bounds are invalid.")
    for attempt in range(1, max_attempts + 1):
        try:
            await run()
            raise BackgroundTaskStoppedError
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - isolate the owned lifetime.
            failure_class = classify_declared_failure(error)
            terminal = (
                failure_class is StartupFailureClass.PERMANENT
                or attempt == max_attempts
            )
            delay = None if terminal else min(retry_seconds * 2 ** (attempt - 1), 60)
            logger.emit(
                level=LogLevel.CRITICAL if terminal else LogLevel.ERROR,
                event_name=(
                    "approval_background_task_suspended"
                    if terminal
                    else "approval_background_task_retry"
                ),
                fields={
                    "task_name": task_name,
                    "failure_category": failure_class.value,
                    "failure_type": type(error).__name__,
                    "retry_attempt": attempt,
                    "retry_delay_seconds": delay,
                    "terminal": terminal,
                },
            )
            if terminal:
                await asyncio.Event().wait()
                return
            await sleeper(delay or retry_seconds)


__all__ = ("BackgroundTaskStoppedError", "supervise_background_task")
