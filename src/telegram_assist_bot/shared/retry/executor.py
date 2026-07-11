"""Asynchronous bounded retry execution for explicitly safe operations."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import classify_error

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from telegram_assist_bot.shared.retry.policy import RetryPolicy


class AsyncSleeper(Protocol):
    """Describe an injectable asynchronous delay function."""

    def __call__(self, delay_seconds: float, /) -> Awaitable[None]:
        """Sleep for the requested delay."""
        ...


class JitterSource(Protocol):
    """Describe an injectable source returning a value from zero through one."""

    def __call__(self) -> float:
        """Return the next normalized jitter value."""
        ...


class RetryEventLogger(Protocol):
    """Emit structured retry events without coupling to a logging backend."""

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Emit one retry or final-failure event."""
        ...


def _find_cancellation(error: BaseException) -> asyncio.CancelledError | None:
    """Return a cancellation retained in a cycle-safe cause or context chain."""
    current: BaseException | None = error
    visited: set[int] = set()
    while current is not None and id(current) not in visited:
        if isinstance(current, asyncio.CancelledError):
            return current
        visited.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _emit_retry_event(
    logger: RetryEventLogger,
    *,
    level: LogLevel,
    event_name: str,
    fields: Mapping[str, object],
    error: Exception,
) -> bool:
    """Emit one event and report a sink failure without retaining its details."""
    try:
        logger.emit(
            level=level,
            event_name=event_name,
            fields=fields,
            error=error,
        )
    except Exception:  # noqa: BLE001 - preserve the operation error if its sink fails.
        return False
    return True


async def execute_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    *,
    operation_name: str,
    operation_is_safe_to_retry: bool,
    policy: RetryPolicy,
    logger: RetryEventLogger,
    sleeper: AsyncSleeper,
    jitter_source: JitterSource,
) -> T:
    """Run an async operation with bounded, classified, observable retries."""
    if operation_is_safe_to_retry is not True:
        raise ValueError("operation must be explicitly safe to retry")
    if (
        type(operation_name) is not str
        or not operation_name
        or operation_name.isspace()
    ):
        raise ValueError("operation_name must not be blank")

    attempt = 1
    while True:
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            cancellation = _find_cancellation(error)
            if cancellation is not None:
                raise cancellation from None
            classification = classify_error(error)
            common_fields: dict[str, object] = {
                "operation_name": operation_name,
                "attempt": attempt,
                "max_attempts": policy.max_attempts,
            }
            if not classification.retryable or attempt >= policy.max_attempts:
                emitted = _emit_retry_event(
                    logger,
                    level=LogLevel.ERROR,
                    event_name="retry_exhausted",
                    fields={
                        **common_fields,
                        "reason": (
                            "max_attempts_exhausted"
                            if classification.retryable
                            else "non_retryable"
                        ),
                    },
                    error=error,
                )
                if not emitted:
                    error.add_note("Structured retry event emission failed.")
                raise

            delay = policy.delay_for_retry(attempt, random_value=jitter_source())
            emitted = _emit_retry_event(
                logger,
                level=LogLevel.WARNING,
                event_name="retry_scheduled",
                fields={
                    **common_fields,
                    "next_attempt": attempt + 1,
                    "delay_seconds": delay,
                },
                error=error,
            )
            if not emitted:
                error.add_note("Structured retry event emission failed.")
                raise
            await sleeper(delay)
            attempt += 1


__all__ = (
    "AsyncSleeper",
    "JitterSource",
    "RetryEventLogger",
    "execute_with_retry",
)
