"""Application service guarding each external AI provider attempt."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.domain.ai.provider_health import (
    ProviderAttemptOutcome,
    ProviderFailureCategory,
)
from telegram_assist_bot.shared.errors import (
    ApplicationError,
    ConfigurationError,
    ErrorCategory,
    classify_error,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.application.ports.provider_state_repository import (
        ProviderStateRepository,
    )
    from telegram_assist_bot.shared.config import AiProviderGuardConfig


class ProviderTemporarilyUnavailableError(ApplicationError):
    """Report a guarded candidate that cannot start an external attempt yet."""

    safe_message = "The AI provider candidate is temporarily unavailable."

    def __init__(self, reason: str, next_eligible_at: datetime | None) -> None:
        """Retain only a safe reason and nearest known eligibility timestamp."""
        super().__init__()
        self.reason = reason
        self.next_eligible_at = next_eligible_at


class AllProvidersTemporarilyUnavailableError(ApplicationError):
    """Report that no configured candidate can safely start now."""

    safe_message = "All AI provider candidates are temporarily unavailable."

    def __init__(self, next_eligible_at: datetime | None) -> None:
        """Retain the nearest safe eligibility time without provider payloads."""
        super().__init__()
        self.next_eligible_at = next_eligible_at


class ProviderAttemptGuard(Protocol):
    """Application-owned contract for guarding one concrete external attempt."""

    async def execute[T](
        self,
        *,
        provider_name: str,
        model_name: str,
        owner_id: str,
        policy: AiProviderGuardConfig | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute an operation only after acquiring durable capacity."""
        ...


def outcome_from_error(error: BaseException) -> ProviderAttemptOutcome:
    """Map a sanitized application error classification to provider health."""
    if isinstance(error, asyncio.CancelledError):
        return ProviderAttemptOutcome.cancelled_by_caller()
    classification = classify_error(error)
    category = classification.category
    rate_limited = category is ErrorCategory.RATE_LIMIT
    retry_after: float | None = None
    if rate_limited:
        raw_retry_after = getattr(error, "retry_after", None)
        if isinstance(raw_retry_after, str) and raw_retry_after.isascii():
            if raw_retry_after.isdigit():
                retry_after = float(raw_retry_after)
        elif isinstance(raw_retry_after, (int, float)) and raw_retry_after >= 0:
            retry_after = float(raw_retry_after)
    return ProviderAttemptOutcome(
        success=False,
        failure_category=ProviderFailureCategory(category.value),
        health_failure=category
        in {ErrorCategory.TIMEOUT, ErrorCategory.TRANSIENT, ErrorCategory.RATE_LIMIT},
        rate_limited=rate_limited,
        retry_after_seconds=retry_after,
    )


class ProviderGuard:
    """Acquire, hold and record one reservation around every provider call."""

    def __init__(self, repository: ProviderStateRepository, clock: Clock) -> None:
        """Initialize the guard with persistent state and an injected UTC clock."""
        self._repository = repository
        self._clock = clock

    async def execute[T](
        self,
        *,
        provider_name: str,
        model_name: str,
        owner_id: str,
        policy: AiProviderGuardConfig | None,
        operation: Callable[[], Awaitable[T]],
    ) -> T:
        """Execute one external attempt while holding durable provider capacity."""
        if policy is None:
            raise ConfigurationError(
                cause=ValueError("AI candidate guard policy is required")
            )
        now = self._clock.utc_now()
        result = await self._repository.reserve(
            provider_name,
            model_name,
            owner_id,
            now,
            now + timedelta(seconds=policy.reservation_seconds),
            concurrency_limit=policy.concurrency_limit,
            request_window_seconds=policy.request_window_seconds,
            request_limit=policy.request_limit,
        )
        reservation = result.reservation
        if reservation is None:
            reason = result.reason.value if result.reason is not None else "unavailable"
            raise ProviderTemporarilyUnavailableError(
                reason,
                result.next_eligible_at,
            )

        outcome = ProviderAttemptOutcome.cancelled_by_caller()
        try:
            value = await operation()
        except asyncio.CancelledError:
            raise
        except BaseException as error:
            outcome = outcome_from_error(error)
            raise
        else:
            outcome = ProviderAttemptOutcome.succeeded()
            return value
        finally:
            record_task = asyncio.create_task(
                self._repository.record(
                    provider_name,
                    model_name,
                    reservation.reservation_id,
                    owner_id,
                    outcome,
                    self._clock.utc_now(),
                    failure_threshold=policy.failure_threshold,
                    open_seconds=policy.open_seconds,
                    fallback_cooldown_seconds=policy.rate_limit_cooldown_seconds,
                )
            )
            try:
                await asyncio.shield(record_task)
            except asyncio.CancelledError:
                await record_task
                raise


__all__ = (
    "AllProvidersTemporarilyUnavailableError",
    "ProviderAttemptGuard",
    "ProviderGuard",
    "ProviderTemporarilyUnavailableError",
    "outcome_from_error",
)
