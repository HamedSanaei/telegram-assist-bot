"""Authorize and atomically cancel one scheduled publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telegram_assist_bot.domain import (
    CancellationPolicy,
    CancellationResult,
    validate_interval,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.application.ports import ScheduleRepository


@dataclass(frozen=True, slots=True)
class CancelRequest:
    """Carry server-trusted cancellation identity and optimistic version."""

    job_id: str
    destination_id: int
    expected_version: int
    actor_id: int
    correlation_id: str
    authorized: bool


class CancelScheduledPost:
    """Cancel one eligible job and sync UI only after persistence commits."""

    def __init__(
        self,
        repository: ScheduleRepository,
        *,
        clock: Callable[[], datetime],
        interval_seconds: float,
        policy: CancellationPolicy,
        synchronize: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Store cancellation policy and optional post-commit synchronizer."""
        self._repository = repository
        self._clock = clock
        self._interval = validate_interval(interval_seconds)
        self._policy = policy
        self._synchronize = synchronize

    async def execute(self, request: CancelRequest) -> CancellationResult:
        """Return an explicit denial, conflict, or canonical cancellation result."""
        if not request.authorized:
            return CancellationResult.PERMISSION_DENIED
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Cancellation clock must return aware time.")
        result = await self._repository.cancel(
            job_id=request.job_id,
            destination_id=request.destination_id,
            expected_version=request.expected_version,
            policy=self._policy,
            interval=self._interval,
            actor_id=request.actor_id,
            now=now.astimezone(UTC),
            correlation_id=request.correlation_id,
        )
        if result is CancellationResult.CANCELLED and self._synchronize is not None:
            await self._synchronize()
        return result


__all__ = ("CancelRequest", "CancelScheduledPost")
