"""Restart-safe deletion of one successful immediate destination publication."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import PublisherError
from telegram_assist_bot.domain import PublicationFailureCategory

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.application.ports import (
        PublicationRetractionRepository,
        TelegramMessageDeletionGateway,
    )
    from telegram_assist_bot.domain import Publication


class RetractionStatus(StrEnum):
    """Stable outcomes for one retraction worker iteration."""

    IDLE = "idle"
    SUCCEEDED = "succeeded"
    RETRY_PENDING = "retry_pending"
    PERMANENT_FAILED = "permanent_failed"
    LEASE_LOST = "lease_lost"


class RetractImmediatePublication:
    """Claim, delete, and persist one immediate-publication retraction."""

    def __init__(
        self,
        repository: PublicationRetractionRepository,
        gateway: TelegramMessageDeletionGateway,
        *,
        owner: str,
        clock: Callable[[], datetime],
        timeout_seconds: float,
        lease_seconds: float,
        max_attempts: int,
        retry_delay_seconds: float,
        after_result: Callable[[Publication, RetractionStatus], Awaitable[None]]
        | None = None,
    ) -> None:
        """Store explicit persistence, transport, and bounded retry settings."""
        if (
            timeout_seconds <= 0
            or lease_seconds < timeout_seconds
            or not 1 <= max_attempts <= 10
            or retry_delay_seconds <= 0
        ):
            raise ValueError("Publication retraction configuration is invalid.")
        self._repository = repository
        self._gateway = gateway
        self._owner = owner
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._retry_delay_seconds = retry_delay_seconds
        self._after_result = after_result

    async def execute_once(self) -> RetractionStatus:
        """Execute at most one durable retraction request."""
        now = self._now()
        publication = await self._repository.claim_retraction(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
            max_attempts=self._max_attempts,
        )
        if publication is None:
            return RetractionStatus.IDLE
        try:
            await self._gateway.delete(
                publication.destination_id,
                publication.message_ids,
                timeout_seconds=self._timeout_seconds,
            )
        except asyncio.CancelledError:
            raise
        except PublisherError as error:
            retryable = error.category in {
                PublicationFailureCategory.TIMEOUT,
                PublicationFailureCategory.RATE_LIMIT,
                PublicationFailureCategory.TRANSIENT,
                PublicationFailureCategory.AMBIGUOUS,
            }
            next_attempt_at = (
                self._now() + timedelta(seconds=self._retry_delay_seconds)
                if retryable
                and publication.retraction_attempt_count < self._max_attempts
                else None
            )
            failed = await self._repository.fail_retraction(
                publication.publication_id,
                owner=self._owner,
                category=error.category,
                next_attempt_at=next_attempt_at,
                failure_type=type(error).__name__,
                failure_reason_code=error.reason_code,
            )
            status = (
                RetractionStatus.RETRY_PENDING
                if next_attempt_at is not None
                else RetractionStatus.PERMANENT_FAILED
            )
            await self._notify(failed, status)
            return status
        except Exception as error:  # noqa: BLE001 - isolate one deletion candidate.
            next_attempt_at = (
                self._now() + timedelta(seconds=self._retry_delay_seconds)
                if publication.retraction_attempt_count < self._max_attempts
                else None
            )
            failed = await self._repository.fail_retraction(
                publication.publication_id,
                owner=self._owner,
                category=PublicationFailureCategory.AMBIGUOUS,
                next_attempt_at=next_attempt_at,
                failure_type=type(error).__name__,
                failure_reason_code="unhandled_delete_exception",
            )
            status = (
                RetractionStatus.RETRY_PENDING
                if next_attempt_at is not None
                else RetractionStatus.PERMANENT_FAILED
            )
            await self._notify(failed, status)
            return status
        completed = await self._repository.complete_retraction(
            publication.publication_id,
            owner=self._owner,
            at=self._now(),
        )
        await self._notify(completed, RetractionStatus.SUCCEEDED)
        return RetractionStatus.SUCCEEDED

    async def _notify(self, publication: Publication, status: RetractionStatus) -> None:
        if self._after_result is not None:
            await self._after_result(publication, status)

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Publication retraction clock must return aware time.")
        return value.astimezone(UTC)


__all__ = ("RetractImmediatePublication", "RetractionStatus")
