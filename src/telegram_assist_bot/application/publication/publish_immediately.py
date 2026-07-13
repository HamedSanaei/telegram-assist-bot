"""Bounded, idempotent publication of destination-prepared content."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import (
    PublicationClaimOutcome,
    PublicationPayload,
    PublisherError,
)
from telegram_assist_bot.domain import (
    Publication,
    PublicationFailureCategory,
    PublicationState,
    publication_identity,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from telegram_assist_bot.application.ports import (
        PublicationRepository,
        TelegramPublisherGateway,
    )


class PublishStatus(StrEnum):
    """Stable immediate-publication application outcomes."""

    SUCCEEDED = "succeeded"
    ALREADY_PUBLISHED = "already_published"
    BUSY = "busy"
    REJECTED = "rejected"
    RETRY_PENDING = "retry_pending"
    PERMANENT_FAILED = "permanent_failed"
    OUTCOME_UNKNOWN = "outcome_unknown"


@dataclass(frozen=True, slots=True)
class PublishRequest:
    """Carry trusted server-side authorization and destination-ready payload."""

    post_id: str
    destination_id: int
    payload: PublicationPayload
    owner: str
    correlation_id: str
    authorized: bool
    post_publishable: bool
    immediate_selected: bool
    session_valid: bool
    account_premium: bool
    destination_accessible: bool
    action: str = "immediate"


@dataclass(frozen=True, slots=True)
class PublishResult:
    """Return a safe publication decision and canonical state."""

    status: PublishStatus
    publication: Publication | None = None


class PublishImmediately:
    """Claim, publish, and persist one logical publication safely."""

    def __init__(
        self,
        repository: PublicationRepository,
        publisher: TelegramPublisherGateway,
        *,
        clock: Callable[[], datetime],
        sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
        timeout_seconds: float = 30,
        lease_seconds: float = 60,
        max_attempts: int = 3,
        initial_delay_seconds: float = 1,
        maximum_delay_seconds: float = 30,
        jitter_source: Callable[[], float] = lambda: 0.5,
        jitter_ratio: float = 0.2,
    ) -> None:
        """Store explicit boundaries and validate every bounded retry setting."""
        if (
            timeout_seconds <= 0
            or lease_seconds < timeout_seconds
            or not 1 <= max_attempts <= 10
            or initial_delay_seconds <= 0
            or maximum_delay_seconds < initial_delay_seconds
            or not 0 <= jitter_ratio <= 1
        ):
            raise ValueError("Publication retry configuration is invalid.")
        self._repository = repository
        self._publisher = publisher
        self._clock = clock
        self._sleeper = sleeper
        self._timeout_seconds = timeout_seconds
        self._lease_seconds = lease_seconds
        self._max_attempts = max_attempts
        self._initial_delay_seconds = initial_delay_seconds
        self._maximum_delay_seconds = maximum_delay_seconds
        self._jitter_source = jitter_source
        self._jitter_ratio = jitter_ratio

    async def execute(self, request: PublishRequest) -> PublishResult:
        """Publish only an authorized, selected, destination-ready request."""
        if (
            not request.authorized
            or not request.post_publishable
            or not request.immediate_selected
            or not request.session_valid
            or not request.account_premium
            or not request.destination_accessible
            or request.payload.destination_id != request.destination_id
            or (request.payload.text is None and not request.payload.media)
        ):
            return PublishResult(PublishStatus.REJECTED)
        validation_time = self._aware_now()
        if any(
            not item.ready or item.expires_at.astimezone(UTC) <= validation_time
            for item in request.payload.media
        ):
            return PublishResult(PublishStatus.REJECTED)
        identity = publication_identity(
            request.post_id, request.destination_id, request.action
        )
        while True:
            now = self._aware_now()
            claimed = await self._repository.claim(
                publication_id=identity,
                post_id=request.post_id,
                destination_id=request.destination_id,
                owner=request.owner,
                now=now,
                lease_until=now + timedelta(seconds=self._lease_seconds),
                max_attempts=self._max_attempts,
                correlation_id=request.correlation_id,
                action=request.action,
            )
            if claimed.outcome is PublicationClaimOutcome.TERMINAL:
                status = (
                    PublishStatus.ALREADY_PUBLISHED
                    if claimed.publication.state is PublicationState.SUCCEEDED
                    else self._terminal_status(claimed.publication.state)
                )
                return PublishResult(status, claimed.publication)
            if claimed.outcome in {
                PublicationClaimOutcome.BUSY,
                PublicationClaimOutcome.EXHAUSTED,
            }:
                status = (
                    PublishStatus.BUSY
                    if claimed.outcome is PublicationClaimOutcome.BUSY
                    else PublishStatus.PERMANENT_FAILED
                )
                return PublishResult(status, claimed.publication)
            try:
                published = await self._publisher.publish(
                    request.payload, timeout_seconds=self._timeout_seconds
                )
            except asyncio.CancelledError:
                raise
            except PublisherError as error:
                unknown = error.request_may_have_reached_telegram or (
                    error.category is PublicationFailureCategory.AMBIGUOUS
                )
                retryable = (
                    error.category
                    in {
                        PublicationFailureCategory.TIMEOUT,
                        PublicationFailureCategory.RATE_LIMIT,
                        PublicationFailureCategory.TRANSIENT,
                    }
                    and not unknown
                )
                next_at = None
                if retryable and claimed.publication.attempt_count < self._max_attempts:
                    delay = min(
                        self._maximum_delay_seconds,
                        self._initial_delay_seconds
                        * (2 ** max(0, claimed.publication.attempt_count - 1)),
                    )
                    if error.retry_after_seconds is not None:
                        delay = min(
                            self._maximum_delay_seconds, error.retry_after_seconds
                        )
                    random_value = self._jitter_source()
                    if not 0 <= random_value <= 1:
                        raise ValueError(
                            "Publication jitter source is invalid."
                        ) from None
                    delay *= (
                        1 - self._jitter_ratio + (2 * self._jitter_ratio * random_value)
                    )
                    next_at = self._aware_now() + timedelta(seconds=delay)
                failed = await self._repository.fail(
                    identity,
                    owner=request.owner,
                    category=error.category,
                    now=self._aware_now(),
                    next_attempt_at=next_at,
                    outcome_unknown=unknown,
                    failure_type=type(error).__name__,
                )
                if next_at is None:
                    return PublishResult(self._terminal_status(failed.state), failed)
                await self._sleeper(
                    max(0.0, (next_at - self._aware_now()).total_seconds())
                )
                continue
            completed = await self._repository.complete(
                identity, owner=request.owner, result=published
            )
            return PublishResult(PublishStatus.SUCCEEDED, completed)

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("Publication clock must return aware time.")
        return value.astimezone(UTC)

    @staticmethod
    def _terminal_status(state: PublicationState) -> PublishStatus:
        if state is PublicationState.OUTCOME_UNKNOWN:
            return PublishStatus.OUTCOME_UNKNOWN
        if state is PublicationState.WAITING_FOR_RETRY:
            return PublishStatus.RETRY_PENDING
        return PublishStatus.PERMANENT_FAILED


__all__ = ("PublishImmediately", "PublishRequest", "PublishResult", "PublishStatus")
