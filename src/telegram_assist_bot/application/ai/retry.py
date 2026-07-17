"""AI retry execution logic for a single candidate attempt."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.shared.errors import classify_error
from telegram_assist_bot.shared.retry.policy import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class AsyncSleeper(Protocol):
    """Protocol for injecting sleep function."""

    def __call__(self, delay: float) -> Awaitable[None]:
        """Sleep for the requested delay."""
        ...


class JitterSource(Protocol):
    """Protocol for injecting jitter source (returns float between 0.0 and 1.0)."""

    def __call__(self) -> float:
        """Return the next normalized jitter value."""
        ...


async def execute_candidate_with_retry[T](
    operation: Callable[[], Awaitable[T]],
    max_attempts: int,
    sleeper: AsyncSleeper,
    jitter_source: JitterSource,
    initial_delay: float = 1.0,
    max_delay: float = 10.0,
) -> T:
    """Executes an operation with bounded retries and backoff/jitter.

    Only retryable/transient exceptions will trigger retries.
    """
    policy = RetryPolicy(
        max_attempts=max_attempts,
        initial_delay_seconds=initial_delay,
        max_delay_seconds=max_delay,
        backoff_multiplier=2.0,
        jitter_ratio=0.1,
    )

    attempt = 1
    while True:
        try:
            return await operation()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Check for cancellation nested inside causes
            cancellation = _find_cancellation(error)
            if cancellation is not None:
                raise cancellation from None

            classification = classify_error(error)
            if not classification.retryable or attempt >= max_attempts:
                # Bubble up classification
                raise

            # Calculate backoff delay
            delay = policy.delay_for_retry(attempt, random_value=jitter_source())
            await sleeper(delay)
            attempt += 1


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
