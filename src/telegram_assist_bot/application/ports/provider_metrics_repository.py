"""Application-owned atomic Provider/Model metrics contract."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime


class ProviderMetricsRepositoryError(Exception):
    """Sanitized metrics side-effect failure without driver details."""


@dataclass(frozen=True, slots=True)
class ProviderMetricDelta:
    """Non-negative cumulative metric increments for one Provider/Model."""

    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_count: int = 0
    invalid_response_count: int = 0
    fallback_participation_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cumulative_latency_seconds: float = 0.0
    latency_sample_count: int = 0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None

    def __post_init__(self) -> None:
        """Prevent decrements or contradictory token/latency values."""
        numeric = (
            self.request_count,
            self.success_count,
            self.failure_count,
            self.timeout_count,
            self.rate_limit_count,
            self.invalid_response_count,
            self.fallback_participation_count,
            self.input_tokens,
            self.output_tokens,
            self.total_tokens,
            self.cumulative_latency_seconds,
            self.latency_sample_count,
        )
        if any(value < 0 for value in numeric):
            raise ValueError("provider metric increments must not be negative")


@dataclass(frozen=True, slots=True)
class ProviderMetrics:
    """Safe cumulative Provider/Model metrics with derived average latency."""

    provider_name: str
    model_name: str
    request_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    timeout_count: int = 0
    rate_limit_count: int = 0
    invalid_response_count: int = 0
    fallback_participation_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cumulative_latency_seconds: float = 0.0
    latency_sample_count: int = 0
    last_success_at: datetime | None = None
    last_error_at: datetime | None = None

    @property
    def average_latency_seconds(self) -> float | None:
        """Compute—not persist—the average latency from cumulative values."""
        if self.latency_sample_count == 0:
            return None
        return self.cumulative_latency_seconds / self.latency_sample_count


class ProviderMetricsRepository(Protocol):
    """Atomically update independent Provider/Model cumulative metrics."""

    async def increment(
        self, provider_name: str, model_name: str, delta: ProviderMetricDelta
    ) -> ProviderMetrics:
        """Atomically apply one non-negative metric delta."""
        ...

    async def get(self, provider_name: str, model_name: str) -> ProviderMetrics | None:
        """Read metrics using safe compatibility defaults."""
        ...


__all__ = (
    "ProviderMetricDelta",
    "ProviderMetrics",
    "ProviderMetricsRepository",
    "ProviderMetricsRepositoryError",
)
