"""AI domain models."""

from telegram_assist_bot.domain.ai.provider_health import (
    ActiveReservation,
    CircuitState,
    Eligibility,
    IneligibilityReason,
    ProviderAttemptOutcome,
    ProviderFailureCategory,
    ProviderHealth,
    ReservationKind,
)

__all__ = (
    "ActiveReservation",
    "CircuitState",
    "Eligibility",
    "IneligibilityReason",
    "ProviderAttemptOutcome",
    "ProviderFailureCategory",
    "ProviderHealth",
    "ReservationKind",
)
