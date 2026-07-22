"""Application port for persistent Provider/Model health state."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.ai.provider_health import (
        ActiveReservation,
        IneligibilityReason,
        ProviderAttemptOutcome,
        ProviderHealth,
    )


@dataclass(frozen=True, slots=True)
class ProviderReservationResult:
    """Atomic reservation result with a safe temporary-rejection reason."""

    reservation: ActiveReservation | None
    reason: IneligibilityReason | None = None
    next_eligible_at: datetime | None = None


class ProviderStateRepositoryError(RuntimeError):
    """Report a sanitized provider-state persistence failure."""


class ProviderStateRepository(ABC):
    """Persist and atomically mutate provider health documents."""

    @abstractmethod
    async def get_or_create(
        self, provider_name: str, model_name: str
    ) -> ProviderHealth:
        """Read state, creating backward-compatible defaults when absent."""

    @abstractmethod
    async def reserve(
        self,
        provider_name: str,
        model_name: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
        *,
        concurrency_limit: int,
        request_window_seconds: int,
        request_limit: int,
    ) -> ProviderReservationResult:
        """Atomically reclaim expiry, enforce policy and append a reservation."""

    @abstractmethod
    async def record(
        self,
        provider_name: str,
        model_name: str,
        reservation_id: str,
        owner_id: str,
        outcome: ProviderAttemptOutcome,
        now: datetime,
        *,
        failure_threshold: int,
        open_seconds: int,
        fallback_cooldown_seconds: int | None,
    ) -> ProviderHealth:
        """Idempotently release one owned reservation and record its outcome."""


__all__ = (
    "ProviderReservationResult",
    "ProviderStateRepository",
    "ProviderStateRepositoryError",
)
