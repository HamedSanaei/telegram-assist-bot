"""Recover native scheduling cycles without replaying ambiguous Telegram sends."""

from __future__ import annotations

import asyncio
import errno
from random import random
from time import monotonic
from typing import TYPE_CHECKING

from pymongo.errors import ConnectionFailure
from telethon import errors  # type: ignore[import-untyped]

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import (
    ErrorCategory,
    ErrorClassification,
    classify_error,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from telegram_assist_bot.application.native_scheduling import RunNativeScheduling
    from telegram_assist_bot.shared.retry import (
        AsyncSleeper,
        JitterSource,
        RetryEventLogger,
        RetryPolicy,
    )

_NETWORK_ERRNOS = frozenset(
    {
        errno.ECONNABORTED,
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.EHOSTUNREACH,
        errno.ENETUNREACH,
        errno.EPIPE,
        errno.ETIMEDOUT,
    }
)


def classify_native_scheduling_failure(error: BaseException) -> ErrorClassification:
    """Recognize transport failures, not arbitrary MongoDB/RPC/programming errors."""
    if isinstance(error, ConnectionFailure):
        # NetworkTimeout, AutoReconnect and ServerSelectionTimeoutError share
        # this driver base. OperationFailure/ConfigurationError do not.
        category = ErrorCategory.TIMEOUT if error.timeout else ErrorCategory.TRANSIENT
        return ErrorClassification(category=category, retryable=True)
    if isinstance(error, errors.TimedOutError):
        return ErrorClassification(category=ErrorCategory.TIMEOUT, retryable=True)
    if isinstance(error, ConnectionError | errors.ServerError) or (
        isinstance(error, OSError) and error.errno in _NETWORK_ERRNOS
    ):
        return ErrorClassification(category=ErrorCategory.TRANSIENT, retryable=True)
    return classify_error(error)


async def run_native_scheduling_cycle(
    native: RunNativeScheduling,
    *,
    policy: RetryPolicy,
    logger: RetryEventLogger,
    sleeper: AsyncSleeper = asyncio.sleep,
    jitter_source: JitterSource = random,
    monotonic_clock: Callable[[], float] = monotonic,
) -> None:
    """Wait through transport outages with capped delays until one cycle succeeds.

    This retries durable claims, never the Telegram operation itself. The use
    case persists request uncertainty first; an unacknowledged MongoDB write
    retains the existing lease/request_started quarantine. Telethon's existing
    bounded auto_reconnect remains the sole connection/session owner.
    """
    if (
        policy.max_attempts < 2
        or policy.initial_delay_seconds <= 0
        or policy.max_delay_seconds <= 0
    ):
        raise ValueError("Native scheduling recovery requires a positive retry delay.")
    attempt = 0
    failed_at: float | None = None
    while True:
        phase = "execute"
        try:
            await native.execute_once()
            phase = "reconcile"
            await native.reconcile_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            classification = classify_native_scheduling_failure(error)
            if not classification.retryable:
                raise
            attempt += 1
            now = monotonic_clock()
            if failed_at is None:
                failed_at = now
            # The shared policy bounds exponent computation as well as delay.
            # A long outage stays at its final bounded step, not a busy loop or
            # a process restart after a fixed number of recoverable failures.
            delay = max(
                policy.delay_for_retry(
                    min(attempt, policy.max_attempts - 1), random_value=jitter_source()
                ),
                float(getattr(error, "retry_after_seconds", 0)),
            )
            logger.emit(
                level=LogLevel.WARNING,
                event_name="native_schedule_cycle_retry",
                fields={
                    "failure_category": classification.category.value,
                    "failure_type": type(error).__name__,
                    "failure_module": type(error).__module__,
                    "phase": phase,
                    "attempt": attempt,
                    "delay_seconds": delay,
                    "recovery_elapsed_seconds": max(0.0, now - failed_at),
                },
            )
            await sleeper(delay)
        else:
            if failed_at is not None:
                logger.emit(
                    level=LogLevel.INFO,
                    event_name="native_schedule_cycle_recovered",
                    fields={
                        "attempts": attempt,
                        "recovery_elapsed_seconds": max(
                            0.0, monotonic_clock() - failed_at
                        ),
                    },
                )
            return


__all__ = ("classify_native_scheduling_failure", "run_native_scheduling_cycle")
