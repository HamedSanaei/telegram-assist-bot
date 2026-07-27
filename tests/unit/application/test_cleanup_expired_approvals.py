"""Behavioral tests for bounded expired approval-message cleanup."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.cleanup_expired_approvals import (
    ApprovalCleanupLoop,
    CleanupExpiredApprovals,
)
from telegram_assist_bot.application.ports import (
    ApprovalCleanupClaim,
    ApprovalDeleteOutcome,
    ApprovalDeleteTransientError,
    ApprovalDeleteUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class FakeCleanupRepository:
    """Keep cleanup state in memory for application behavior tests."""

    def __init__(self, claims: list[ApprovalCleanupClaim]) -> None:
        self.claims = claims
        self.deleted: dict[str, set[int]] = {
            item.reference_id: set(item.deleted_message_ids) for item in claims
        }
        self.completed: dict[str, str] = {}
        self.deferred: list[str] = []
        self.expired_ui: list[str] = []
        self.backfills: list[tuple[datetime, int, int]] = []
        self.claimed: set[str] = set()

    async def backfill_legacy_expirations(
        self, *, now: datetime, retention_days: int, limit: int
    ) -> int:
        self.backfills.append((now, retention_days, limit))
        return 0

    async def claim_expired(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> ApprovalCleanupClaim | None:
        del owner, lease_until
        for claim in self.claims:
            if (
                claim.expires_at <= now
                and claim.reference_id not in self.claimed
                and claim.reference_id not in self.completed
            ):
                self.claimed.add(claim.reference_id)
                return replace(
                    claim,
                    deleted_message_ids=frozenset(self.deleted[claim.reference_id]),
                )
        return None

    async def expire_ui(self, claim: ApprovalCleanupClaim, *, owner: str) -> bool:
        del owner
        self.expired_ui.append(claim.reference_id)
        return True

    async def recheck_claim(
        self, reference_id: str, *, owner: str, now: datetime
    ) -> ApprovalCleanupClaim | None:
        del owner, now
        claim = next(item for item in self.claims if item.reference_id == reference_id)
        return replace(claim, deleted_message_ids=frozenset(self.deleted[reference_id]))

    async def record_deleted_message(
        self, reference_id: str, message_id: int, *, owner: str
    ) -> bool:
        del owner
        self.deleted[reference_id].add(message_id)
        return True

    async def complete_cleanup(
        self, reference_id: str, *, owner: str, outcome: str
    ) -> bool:
        del owner
        self.completed[reference_id] = outcome
        return True

    async def defer_cleanup(
        self,
        reference_id: str,
        *,
        owner: str,
        next_attempt_at: datetime,
        category: str,
    ) -> bool:
        del owner, next_attempt_at, category
        self.deferred.append(reference_id)
        return True


class FakeDeleteGateway:
    """Record only message identifiers supplied by cleanup."""

    def __init__(
        self,
        failures: dict[int, list[BaseException]] | None = None,
    ) -> None:
        self.calls: list[tuple[int, int]] = []
        self.failures = failures or {}

    async def delete_approval_message(
        self, chat_id: int, message_id: int
    ) -> ApprovalDeleteOutcome:
        self.calls.append((chat_id, message_id))
        failures = self.failures.get(message_id, [])
        if failures:
            raise failures.pop(0)
        return ApprovalDeleteOutcome.DELETED


def _claim(
    reference_id: str,
    *,
    expires_at: datetime,
    chat_id: int = 7,
    message_ids: tuple[int, ...] = (10, 11),
) -> ApprovalCleanupClaim:
    return ApprovalCleanupClaim(
        reference_id,
        f"post-{reference_id}",
        chat_id,
        chat_id,
        message_ids,
        frozenset(),
        expires_at,
    )


def _cleanup(
    repository: FakeCleanupRepository,
    gateway: FakeDeleteGateway,
    clock: Callable[[], datetime],
    *,
    sleeper: Callable[[float], Awaitable[None]] | None = None,
) -> CleanupExpiredApprovals:
    cleanup = CleanupExpiredApprovals(
        repository,
        gateway,
        owner="worker",
        clock=clock,
        retention_days=2,
        lease_seconds=60,
        retry_seconds=1,
        max_attempts=3,
    )
    if sleeper is None:
        return cleanup
    return CleanupExpiredApprovals(
        repository,
        gateway,
        owner="worker",
        clock=clock,
        retention_days=2,
        lease_seconds=60,
        retry_seconds=1,
        max_attempts=3,
        sleeper=sleeper,
    )


def test_fresh_approval_is_preserved_and_exact_boundary_is_deleted() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    repository = FakeCleanupRepository(
        [
            _claim("fresh", expires_at=now + timedelta(microseconds=1)),
            _claim("boundary", expires_at=now),
        ]
    )
    gateway = FakeDeleteGateway()

    processed = asyncio.run(
        _cleanup(repository, gateway, lambda: now).execute_batch(batch_size=5)
    )

    assert processed == 1
    assert repository.completed == {"boundary": "deleted"}
    assert gateway.calls == [(7, 10), (7, 11)]
    assert repository.backfills == [(now, 2, 5)]


def test_all_album_control_messages_and_multiple_admins_are_isolated() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    repository = FakeCleanupRepository(
        [
            _claim("admin-a", expires_at=now, chat_id=7, message_ids=(1, 2, 3, 4)),
            _claim("admin-b", expires_at=now, chat_id=8, message_ids=(5, 6)),
        ]
    )
    gateway = FakeDeleteGateway({2: [ApprovalDeleteUnavailableError("unavailable")]})

    asyncio.run(_cleanup(repository, gateway, lambda: now).execute_batch(batch_size=10))

    assert repository.completed == {
        "admin-a": "unavailable",
        "admin-b": "deleted",
    }
    assert gateway.calls == [(7, 1), (7, 2), (7, 3), (7, 4), (8, 5), (8, 6)]
    assert repository.expired_ui == ["admin-a", "admin-b"]


def test_transient_failure_retries_without_stopping_other_messages() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    repository = FakeCleanupRepository(
        [_claim("retry", expires_at=now, message_ids=(20, 21))]
    )
    gateway = FakeDeleteGateway(
        {20: [ApprovalDeleteTransientError("temporary"), TimeoutError()]}
    )
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    asyncio.run(
        _cleanup(repository, gateway, lambda: now, sleeper=sleep).execute_batch(
            batch_size=1
        )
    )

    assert gateway.calls == [(7, 20), (7, 20), (7, 20), (7, 21)]
    assert sleeps == [1, 2]
    assert repository.completed == {"retry": "deleted"}


def test_exhausted_message_is_deferred_after_remaining_message_succeeds() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    repository = FakeCleanupRepository(
        [_claim("defer", expires_at=now, message_ids=(30, 31))]
    )
    gateway = FakeDeleteGateway(
        {
            30: [
                ApprovalDeleteTransientError("temporary"),
                ApprovalDeleteTransientError("temporary"),
                ApprovalDeleteTransientError("temporary"),
            ]
        }
    )

    async def sleep(_: float) -> None:
        return None

    asyncio.run(
        _cleanup(repository, gateway, lambda: now, sleeper=sleep).execute_batch(
            batch_size=1
        )
    )

    assert gateway.calls[-1] == (7, 31)
    assert repository.deleted["defer"] == {31}
    assert repository.deferred == ["defer"]
    assert repository.completed == {}


def test_loop_cancellation_during_interval_is_clean() -> None:
    now = datetime(2026, 7, 27, 12, tzinfo=UTC)
    repository = FakeCleanupRepository([])
    gateway = FakeDeleteGateway()

    async def cancel_sleep(_: float) -> None:
        raise asyncio.CancelledError

    loop = ApprovalCleanupLoop(
        _cleanup(repository, gateway, lambda: now),
        batch_size=3,
        interval_seconds=60,
        sleeper=cancel_sleep,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run())
    assert repository.backfills == [(now, 2, 3)]
