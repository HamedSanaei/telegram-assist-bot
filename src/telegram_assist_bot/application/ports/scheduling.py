"""Application-owned persistent scheduling boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from telegram_assist_bot.domain import (
        CancellationPolicy,
        CancellationResult,
        ScheduledPublication,
    )


@dataclass(frozen=True, slots=True)
class ScheduleReservation:
    """Return a canonical job and whether it was newly scheduled."""

    job: ScheduledPublication
    created: bool


class ScheduleRepository(Protocol):
    """Persist per-destination slots and lease-based job execution."""

    async def reserve(
        self,
        *,
        job_id: str,
        post_id: str,
        destination_id: int,
        now: datetime,
        interval: timedelta,
    ) -> ScheduleReservation:
        """Reserve one idempotent ordered destination slot."""
        ...

    async def reserve_immediate(
        self,
        *,
        job_id: str,
        post_id: str,
        destination_id: int,
        now: datetime,
    ) -> ScheduleReservation:
        """Reserve one idempotent due-now publication command."""
        ...

    async def get(self, job_id: str) -> ScheduledPublication | None:
        """Load one durable command for cancellation orchestration."""
        ...

    async def claim_due(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        action: str = "scheduled",
    ) -> ScheduledPublication | None:
        """Claim the oldest eligible due job atomically."""
        ...

    async def complete(self, job_id: str, *, owner: str, at: datetime) -> bool:
        """Complete an owned job conditionally."""
        ...

    async def defer(
        self,
        job_id: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        category: str,
        failure_type: str | None = None,
    ) -> bool:
        """Return an owned job to bounded retry waiting."""
        ...

    async def fail(
        self,
        job_id: str,
        *,
        owner: str,
        category: str,
        failure_type: str | None = None,
    ) -> bool:
        """Terminally fail an owned job."""
        ...

    async def cancel(
        self,
        *,
        job_id: str,
        destination_id: int,
        expected_version: int,
        policy: CancellationPolicy,
        interval: timedelta,
        actor_id: int,
        now: datetime,
        correlation_id: str,
    ) -> CancellationResult:
        """Cancel and optionally recompact eligible later jobs."""
        ...


__all__ = ("ScheduleRepository", "ScheduleReservation")
