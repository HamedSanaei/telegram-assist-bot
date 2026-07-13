"""Execute one atomically claimed due publication job."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.publication import PublishStatus
from telegram_assist_bot.shared.config import LogLevel

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.application.ports import ScheduleRepository
    from telegram_assist_bot.application.publication import (
        PublishRequest,
        PublishResult,
    )
    from telegram_assist_bot.domain import ScheduledPublication
    from telegram_assist_bot.shared.observability import StructuredLogger


class RunDueStatus(StrEnum):
    """Describe one worker iteration without exposing persistence details."""

    IDLE = "idle"
    COMPLETED = "completed"
    DEFERRED = "deferred"
    FAILED = "failed"
    LEASE_LOST = "lease_lost"


class RunDuePublication:
    """Claim oldest due work and delegate sending to the idempotent publisher."""

    def __init__(
        self,
        repository: ScheduleRepository,
        *,
        owner: str,
        clock: Callable[[], datetime],
        lease_seconds: float,
        max_attempts: int,
        retry_delay_seconds: float,
        action: str = "scheduled",
        build_request: Callable[[str, int], Awaitable[PublishRequest]],
        publish: Callable[[PublishRequest], Awaitable[PublishResult]],
        after_result: Callable[[ScheduledPublication, RunDueStatus], Awaitable[None]]
        | None = None,
        before_attempt: Callable[[ScheduledPublication], Awaitable[None]] | None = None,
        logger: StructuredLogger | None = None,
    ) -> None:
        """Validate and store explicit worker execution boundaries."""
        if (
            lease_seconds <= 0
            or not 1 <= max_attempts <= 10
            or retry_delay_seconds <= 0
        ):
            raise ValueError("Schedule worker configuration is invalid.")
        self._repository = repository
        self._owner = owner
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds
        if action not in {"immediate", "scheduled"}:
            raise ValueError("Publication command action is invalid.")
        self._action = action
        self._build_request = build_request
        self._publish = publish
        self._after_result = after_result
        self._before_attempt = before_attempt
        self._logger = logger

    async def execute_once(self) -> RunDueStatus:
        """Execute at most one due job; cancellation propagates without completion."""
        now = self._now()
        job = await self._repository.claim_due(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
            action=self._action,
        )
        if job is None:
            return RunDueStatus.IDLE
        self._emit("publication_job_claimed", job)
        if self._before_attempt is not None:
            await self._before_attempt(job)
        self._emit("publication_attempt_started", job)
        try:
            request = await self._build_request(job.post_id, job.destination_id)
            result = await self._publish(request)
        except asyncio.CancelledError:
            raise
        if result.status in {PublishStatus.SUCCEEDED, PublishStatus.ALREADY_PUBLISHED}:
            changed = await self._repository.complete(
                job.job_id, owner=self._owner, at=self._now()
            )
            status = RunDueStatus.COMPLETED if changed else RunDueStatus.LEASE_LOST
            self._emit("publication_succeeded", job)
            if changed:
                self._emit("publication_job_completed", job)
            await self._notify(job, status)
            return status
        if (
            result.status in {PublishStatus.RETRY_PENDING, PublishStatus.BUSY}
            and job.attempt_count < self._max_attempts
        ):
            changed = await self._repository.defer(
                job.job_id,
                owner=self._owner,
                next_attempt_at=self._now()
                + timedelta(seconds=self._retry_delay_seconds),
                category=result.status.value,
            )
            status = RunDueStatus.DEFERRED if changed else RunDueStatus.LEASE_LOST
            self._emit("publication_deferred", job)
            await self._notify(job, status)
            return status
        category = (
            "ambiguous"
            if result.status is PublishStatus.OUTCOME_UNKNOWN
            else result.status.value
        )
        changed = await self._repository.fail(
            job.job_id, owner=self._owner, category=category
        )
        status = RunDueStatus.FAILED if changed else RunDueStatus.LEASE_LOST
        self._emit("publication_failed", job)
        await self._notify(job, status)
        return status

    async def _notify(self, job: ScheduledPublication, status: RunDueStatus) -> None:
        if self._after_result is not None:
            await self._after_result(job, status)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Worker clock must return aware time.")
        return value.astimezone(UTC)

    def _emit(self, event_name: str, job: ScheduledPublication) -> None:
        if self._logger is not None:
            self._logger.emit(
                level=LogLevel.INFO,
                event_name=event_name,
                fields={
                    "approval_post_id": job.post_id,
                    "target_destination_id": job.destination_id,
                    "publication_action": job.action,
                    "scheduled_due_at": job.due_at.isoformat(),
                    "attempt_count": job.attempt_count,
                },
            )


__all__ = ("RunDuePublication", "RunDueStatus")
