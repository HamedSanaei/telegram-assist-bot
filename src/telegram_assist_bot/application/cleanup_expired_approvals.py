"""Bounded cleanup of expired Bot-owned approval messages."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports.approval_cleanup import (
    ApprovalCleanupClaim,
    ApprovalCleanupRepository,
    ApprovalDeleteRateLimitError,
    ApprovalDeleteTransientError,
    ApprovalDeleteUnavailableError,
    ApprovalMessageDeleteGateway,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

type Sleeper = Callable[[float], Awaitable[None]]


class CleanupExpiredApprovals:
    """Process a bounded batch while isolating each approval reference."""

    def __init__(
        self,
        repository: ApprovalCleanupRepository,
        gateway: ApprovalMessageDeleteGateway,
        *,
        owner: str,
        clock: Callable[[], datetime],
        retention_days: int,
        lease_seconds: float,
        retry_seconds: float,
        max_attempts: int,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        """Store cleanup collaborators and bounded retry policy."""
        if retention_days < 1 or lease_seconds <= 0 or retry_seconds <= 0:
            raise ValueError("Approval cleanup timing must be positive.")
        if not 1 <= max_attempts <= 10:
            raise ValueError("Approval cleanup attempts must be between 1 and 10.")
        self._repository = repository
        self._gateway = gateway
        self._owner = owner
        self._clock = clock
        self._retention_days = retention_days
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds
        self._max_attempts = max_attempts
        self._sleeper = sleeper

    async def execute_batch(self, *, batch_size: int) -> int:
        """Backfill and process at most ``batch_size`` persisted references."""
        if batch_size < 1:
            raise ValueError("Approval cleanup batch size must be positive.")
        await self._repository.backfill_legacy_expirations(
            now=self._clock(),
            retention_days=self._retention_days,
            limit=batch_size,
        )
        processed = 0
        for _ in range(batch_size):
            now = self._clock()
            claim = await self._repository.claim_expired(
                owner=self._owner,
                now=now,
                lease_until=now + timedelta(seconds=self._lease_seconds),
            )
            if claim is None:
                break
            await self._process_claim(claim)
            processed += 1
        return processed

    async def _process_claim(self, claim: ApprovalCleanupClaim) -> None:
        if not await self._repository.expire_ui(claim, owner=self._owner):
            return
        current = await self._repository.recheck_claim(
            claim.reference_id, owner=self._owner, now=self._clock()
        )
        if current is None:
            return
        unavailable = False
        retryable_failure: str | None = None
        for message_id in current.message_ids:
            if message_id in current.deleted_message_ids:
                continue
            try:
                await self._delete_with_retry(current.chat_id, message_id)
            except ApprovalDeleteUnavailableError:
                unavailable = True
                continue
            except (ApprovalDeleteTransientError, TimeoutError) as error:
                retryable_failure = getattr(error, "error_category", "timeout")
                continue
            await self._repository.record_deleted_message(
                current.reference_id, message_id, owner=self._owner
            )
        if retryable_failure is not None:
            await self._repository.defer_cleanup(
                current.reference_id,
                owner=self._owner,
                next_attempt_at=self._clock() + timedelta(seconds=self._retry_seconds),
                category=retryable_failure,
            )
            return
        await self._repository.complete_cleanup(
            current.reference_id,
            owner=self._owner,
            outcome="unavailable" if unavailable else "deleted",
        )

    async def _delete_with_retry(self, chat_id: int, message_id: int) -> None:
        for attempt in range(1, self._max_attempts + 1):
            try:
                await self._gateway.delete_approval_message(chat_id, message_id)
                return
            except ApprovalDeleteUnavailableError:
                raise
            except (ApprovalDeleteTransientError, TimeoutError) as error:
                if attempt >= self._max_attempts:
                    raise
                delay = self._retry_seconds * (2 ** (attempt - 1))
                if isinstance(error, ApprovalDeleteRateLimitError):
                    delay = max(delay, float(error.retry_after_seconds))
                await self._sleeper(min(delay, self._lease_seconds))


class ApprovalCleanupLoop:
    """Run bounded MongoDB-backed cleanup iterations until cancellation."""

    def __init__(
        self,
        cleanup: CleanupExpiredApprovals,
        *,
        batch_size: int,
        interval_seconds: float,
        sleeper: Sleeper = asyncio.sleep,
    ) -> None:
        """Store one cleanup use case and bounded iteration settings."""
        if batch_size < 1 or interval_seconds <= 0:
            raise ValueError("Approval cleanup loop bounds must be positive.")
        self._cleanup = cleanup
        self._batch_size = batch_size
        self._interval_seconds = interval_seconds
        self._sleeper = sleeper

    async def run(self) -> None:
        """Execute one bounded batch per configured interval."""
        while True:
            await self._cleanup.execute_batch(batch_size=self._batch_size)
            await self._sleeper(self._interval_seconds)


__all__ = ("ApprovalCleanupLoop", "CleanupExpiredApprovals")
