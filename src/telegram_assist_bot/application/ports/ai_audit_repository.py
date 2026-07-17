"""Sanitized immutable AI audit-event persistence contract."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ai.contracts import AITaskType


class AIAuditEventType(StrEnum):
    """Allowlisted audit events emitted by the isolated AI executor."""

    CACHE_HIT = "cache_hit"
    CACHE_MISS = "cache_miss"
    CACHE_SIDE_EFFECT_FAILED = "cache_side_effect_failed"
    PROVIDER_ATTEMPT = "provider_attempt"
    INTERNAL_RETRY = "internal_retry"
    MODEL_FALLBACK = "model_fallback"
    PROVIDER_FALLBACK = "provider_fallback"
    NORMALIZED_RESULT = "normalized_result"
    INVALID_PROVIDER_RESPONSE = "invalid_provider_response"
    FINAL_FAILURE = "final_failure"
    ADVERTISEMENT_RESULT_APPLIED = "advertisement_result_applied"
    ADVERTISEMENT_FAILURE_POLICY_APPLIED = "advertisement_failure_policy_applied"


class AIAuditRepositoryError(Exception):
    """Sanitized audit side-effect failure without persistence details."""


@dataclass(frozen=True, slots=True)
class AIAuditEvent:
    """Immutable safe metadata for one idempotent AI execution event."""

    event_id: str
    event_type: AIAuditEventType
    job_id: str
    post_id: str
    task_type: AITaskType
    prompt_version: str
    schema_version: str
    occurred_at: datetime
    provider_name: str | None = None
    model_name: str | None = None
    sequence_number: int | None = None
    attempt_number: int | None = None
    retry_count: int = 0
    fallback_count: int = 0
    success: bool | None = None
    failure_category: str | None = None
    http_status: int | None = None
    latency_seconds: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_hit: bool = False
    expires_at: datetime | None = None
    audit_schema_version: int = 1

    def __post_init__(self) -> None:
        """Reject negative counters and invalid safe HTTP status metadata."""
        values = (
            self.sequence_number,
            self.attempt_number,
            self.retry_count,
            self.fallback_count,
            self.input_tokens,
            self.output_tokens,
        )
        if any(value is not None and value < 0 for value in values):
            raise ValueError("audit counters must not be negative")
        if self.http_status is not None and not 100 <= self.http_status <= 599:
            raise ValueError("audit HTTP status is invalid")


class AIAuditRepository(Protocol):
    """Append immutable events with stable-id idempotency."""

    async def append(self, event: AIAuditEvent) -> bool:
        """Return true only when the immutable event was newly inserted."""
        ...


__all__ = (
    "AIAuditEvent",
    "AIAuditEventType",
    "AIAuditRepository",
    "AIAuditRepositoryError",
)
