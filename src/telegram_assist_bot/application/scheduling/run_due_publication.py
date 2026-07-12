"""Execute one atomically claimed due publication job."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.publication import PublishStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.application.ports import ScheduleRepository
    from telegram_assist_bot.application.publication import (
        PublishRequest,
        PublishResult,
    )


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
        build_request: Callable[[str, int], Awaitable[PublishRequest]],
        publish: Callable[[PublishRequest], Awaitable[PublishResult]],
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
        self._build_request = build_request
        self._publish = publish

    async def execute_once(self) -> RunDueStatus:
        """Execute at most one due job; cancellation propagates without completion."""
        now = self._now()
        job = await self._repository.claim_due(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if job is None:
            return RunDueStatus.IDLE
        try:
            request = await self._build_request(job.post_id, job.destination_id)
            result = await self._publish(request)
        except asyncio.CancelledError:
            raise
        if result.status in {PublishStatus.SUCCEEDED, PublishStatus.ALREADY_PUBLISHED}:
            changed = await self._repository.complete(
                job.job_id, owner=self._owner, at=self._now()
            )
            return RunDueStatus.COMPLETED if changed else RunDueStatus.LEASE_LOST
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
            return RunDueStatus.DEFERRED if changed else RunDueStatus.LEASE_LOST
        category = (
            "ambiguous"
            if result.status is PublishStatus.OUTCOME_UNKNOWN
            else result.status.value
        )
        changed = await self._repository.fail(
            job.job_id, owner=self._owner, category=category
        )
        return RunDueStatus.FAILED if changed else RunDueStatus.LEASE_LOST

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Worker clock must return aware time.")
        return value.astimezone(UTC)


__all__ = ("RunDuePublication", "RunDueStatus")
