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
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - isolate malformed job payloads.
            return await self._record_pre_send_failure(job, error)
        try:
            result = await self._publish(request)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - request outcome is ambiguous.
            failure_type = type(error).__name__
            changed = await self._repository.fail(
                job.job_id,
                owner=self._owner,
                category="ambiguous",
                failure_type=failure_type,
                failure_reason_code="unhandled_publish_exception",
            )
            status = RunDueStatus.FAILED if changed else RunDueStatus.LEASE_LOST
            self._emit(
                "publication_failed",
                job,
                failure_category="ambiguous",
                failure_type=failure_type,
                reason_code="unhandled_publish_exception",
            )
            await self._notify(job, status)
            return status
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
            failure_category, failure_type, reason_code = self._failure_details(result)
            changed = await self._repository.defer(
                job.job_id,
                owner=self._owner,
                next_attempt_at=self._now()
                + timedelta(seconds=self._retry_delay_seconds),
                category=failure_category,
                failure_type=failure_type,
                failure_reason_code=reason_code,
            )
            status = RunDueStatus.DEFERRED if changed else RunDueStatus.LEASE_LOST
            self._emit(
                "publication_deferred",
                job,
                failure_category=failure_category,
                failure_type=failure_type,
                reason_code=reason_code,
            )
            await self._notify(job, status)
            return status
        category = (
            "ambiguous"
            if result.status is PublishStatus.OUTCOME_UNKNOWN
            else result.status.value
        )
        failure_category, failure_type, reason_code = self._failure_details(
            result, fallback_category=category
        )
        changed = await self._repository.fail(
            job.job_id,
            owner=self._owner,
            category=failure_category,
            failure_type=failure_type,
            failure_reason_code=reason_code,
        )
        status = RunDueStatus.FAILED if changed else RunDueStatus.LEASE_LOST
        self._emit(
            "publication_failed",
            job,
            failure_category=failure_category,
            failure_type=failure_type,
            reason_code=reason_code,
        )
        await self._notify(job, status)
        return status

    async def _notify(self, job: ScheduledPublication, status: RunDueStatus) -> None:
        if self._after_result is not None:
            await self._after_result(job, status)

    async def _record_pre_send_failure(
        self, job: ScheduledPublication, error: Exception
    ) -> RunDueStatus:
        failure_type = type(error).__name__
        failure_category = "preparation_failure"
        if job.attempt_count < self._max_attempts:
            changed = await self._repository.defer(
                job.job_id,
                owner=self._owner,
                next_attempt_at=self._now()
                + timedelta(seconds=self._retry_delay_seconds),
                category=failure_category,
                failure_type=failure_type,
                failure_reason_code="request_build_failed",
            )
            status = RunDueStatus.DEFERRED if changed else RunDueStatus.LEASE_LOST
            self._emit(
                "publication_deferred",
                job,
                failure_category=failure_category,
                failure_type=failure_type,
                reason_code="request_build_failed",
            )
        else:
            changed = await self._repository.fail(
                job.job_id,
                owner=self._owner,
                category=failure_category,
                failure_type=failure_type,
                failure_reason_code="request_build_failed",
            )
            status = RunDueStatus.FAILED if changed else RunDueStatus.LEASE_LOST
            self._emit(
                "publication_failed",
                job,
                failure_category=failure_category,
                failure_type=failure_type,
                reason_code="request_build_failed",
            )
        await self._notify(job, status)
        return status

    @staticmethod
    def _failure_details(
        result: PublishResult, *, fallback_category: str | None = None
    ) -> tuple[str, str, str | None]:
        publication = result.publication
        category = (
            fallback_category
            if fallback_category == "ambiguous"
            else publication.error_category
            if publication is not None and publication.error_category
            else fallback_category or result.status.value
        )
        failure_type = (
            publication.failure_type
            if publication is not None and publication.failure_type
            else "PublishResultFailure"
        )
        reason_code = (
            publication.failure_reason_code if publication is not None else None
        )
        return category, failure_type, reason_code

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Worker clock must return aware time.")
        return value.astimezone(UTC)

    def _emit(
        self,
        event_name: str,
        job: ScheduledPublication,
        *,
        failure_category: str | None = None,
        failure_type: str | None = None,
        reason_code: str | None = None,
    ) -> None:
        if self._logger is not None:
            fields: dict[str, object] = {
                "approval_post_id": job.post_id,
                "target_destination_id": job.destination_id,
                "publication_action": job.action,
                "scheduled_due_at": job.due_at.isoformat(),
                "attempt_count": job.attempt_count,
            }
            if failure_category is not None:
                fields["failure_category"] = failure_category
            if failure_type is not None:
                fields["failure_type"] = failure_type
            if reason_code is not None:
                fields["reason_code"] = reason_code
            level = LogLevel.INFO
            if event_name == "publication_deferred":
                level = LogLevel.WARNING
            elif event_name == "publication_failed":
                level = LogLevel.ERROR
            self._logger.emit(
                level=level,
                event_name=event_name,
                fields=fields,
            )


__all__ = ("RunDuePublication", "RunDueStatus")
