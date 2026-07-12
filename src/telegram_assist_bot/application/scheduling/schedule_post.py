"""Reserve a persistent per-destination publication slot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from telegram_assist_bot.domain import schedule_identity, validate_interval

if TYPE_CHECKING:
    from collections.abc import Callable

    from telegram_assist_bot.application.ports import (
        ScheduleRepository,
        ScheduleReservation,
    )


@dataclass(frozen=True, slots=True)
class ScheduleRequest:
    """Carry trusted authorization and selection state for scheduling."""

    post_id: str
    destination_id: int
    authorized: bool
    post_publishable: bool
    scheduled_selected: bool


class SchedulePost:
    """Validate and reserve exactly one durable schedule identity."""

    def __init__(
        self,
        repository: ScheduleRepository,
        *,
        clock: Callable[[], datetime],
        interval_seconds: float,
    ) -> None:
        """Store the repository, injected clock, and validated interval."""
        self._repository = repository
        self._clock = clock
        self._interval = validate_interval(interval_seconds)

    async def execute(self, request: ScheduleRequest) -> ScheduleReservation:
        """Reject invalid state before touching the persistent queue."""
        if (
            not request.authorized
            or not request.post_publishable
            or not request.scheduled_selected
        ):
            raise PermissionError("Schedule request is not actionable.")
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("Schedule clock must return aware time.")
        return await self._repository.reserve(
            job_id=schedule_identity(request.post_id, request.destination_id),
            post_id=request.post_id,
            destination_id=request.destination_id,
            now=now.astimezone(UTC),
            interval=self._interval,
        )


__all__ = ("SchedulePost", "ScheduleRequest")
