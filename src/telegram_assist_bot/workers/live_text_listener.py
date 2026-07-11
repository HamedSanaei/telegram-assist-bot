"""Cancellation-safe bounded worker for one live Telegram text source."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.application import LiveMessageOutcome
from telegram_assist_bot.application.ports import (
    TelegramLiveGateway,
    TelegramRateLimitError,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import classify_error

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import (
        TelegramLiveSubscription,
        TelegramTextMessage,
    )
    from telegram_assist_bot.shared.retry import (
        AsyncSleeper,
        JitterSource,
        RetryEventLogger,
        RetryPolicy,
    )


class LiveMessageHandler(Protocol):
    """Handle one mapped live message without worker-owned business logic."""

    async def execute(
        self,
        message: TelegramTextMessage,
        *,
        source_channel_id: int,
        correlation_id: str,
    ) -> LiveMessageOutcome:
        """Return one payload-free ingestion outcome."""
        ...


@dataclass(frozen=True, slots=True)
class LiveListenerResult:
    """Report payload-free counts after a bounded stream ends cleanly."""

    created: int
    already_existing: int
    skipped: int
    reconnects: int


@dataclass(slots=True)
class LiveTextListener:
    """Subscribe, consume, and reconnect one canonical source with hard bounds."""

    gateway: TelegramLiveGateway = field(repr=False)
    handler: LiveMessageHandler = field(repr=False)
    retry_policy: RetryPolicy
    logger: RetryEventLogger = field(repr=False)
    sleeper: AsyncSleeper = field(repr=False)
    jitter_source: JitterSource = field(repr=False)
    buffer_size: int
    maximum_flood_wait_seconds: float

    async def run(
        self,
        source_channel_id: int,
        *,
        correlation_id: str,
        initial_subscription: TelegramLiveSubscription | None = None,
    ) -> LiveListenerResult:
        """Run until clean stream completion, permanent failure, or cancellation."""
        if type(self.buffer_size) is not int or not 1 <= self.buffer_size <= 10_000:
            raise ValueError("buffer_size must be between 1 and 10000")
        if self.maximum_flood_wait_seconds < 0:
            raise ValueError("maximum_flood_wait_seconds must not be negative")
        created = 0
        already_existing = 0
        skipped = 0
        attempt = 1
        prepared_subscription = initial_subscription
        while True:
            subscription = None
            try:
                if prepared_subscription is not None:
                    subscription = prepared_subscription
                    prepared_subscription = None
                else:
                    subscription = await self.gateway.subscribe(
                        source_channel_id,
                        buffer_size=self.buffer_size,
                    )
                async for message in subscription:
                    outcome = await self.handler.execute(
                        message,
                        source_channel_id=source_channel_id,
                        correlation_id=correlation_id,
                    )
                    if outcome is LiveMessageOutcome.CREATED:
                        created += 1
                    elif outcome is LiveMessageOutcome.ALREADY_EXISTS:
                        already_existing += 1
                    else:
                        skipped += 1
            except asyncio.CancelledError:
                if subscription is not None:
                    await subscription.close()
                raise
            except Exception as error:
                if subscription is not None:
                    await subscription.close()
                classification = classify_error(error)
                flood_wait_exceeds_cap = (
                    isinstance(error, TelegramRateLimitError)
                    and error.retry_after_seconds > self.maximum_flood_wait_seconds
                )
                if (
                    not classification.retryable
                    or flood_wait_exceeds_cap
                    or attempt >= self.retry_policy.max_attempts
                ):
                    self.logger.emit(
                        level=LogLevel.ERROR,
                        event_name="telegram_listener_failed",
                        fields={
                            "source_channel_id": source_channel_id,
                            "attempt": attempt,
                        },
                        error=error,
                    )
                    raise
                delay = self._retry_delay(error, attempt)
                self.logger.emit(
                    level=LogLevel.WARNING,
                    event_name="telegram_listener_reconnect_scheduled",
                    fields={
                        "source_channel_id": source_channel_id,
                        "attempt": attempt,
                        "next_attempt": attempt + 1,
                        "delay_seconds": delay,
                    },
                    error=error,
                )
                await self.sleeper(delay)
                attempt += 1
                continue
            if subscription is not None:
                await subscription.close()
            return LiveListenerResult(
                created=created,
                already_existing=already_existing,
                skipped=skipped,
                reconnects=attempt - 1,
            )

    def _retry_delay(self, error: Exception, attempt: int) -> float:
        if isinstance(error, TelegramRateLimitError):
            return float(error.retry_after_seconds)
        return self.retry_policy.delay_for_retry(
            attempt,
            random_value=self.jitter_source(),
        )


__all__ = ("LiveListenerResult", "LiveMessageHandler", "LiveTextListener")
