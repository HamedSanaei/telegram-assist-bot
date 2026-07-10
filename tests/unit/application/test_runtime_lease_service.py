"""Unit tests for distributed runtime lease coordination."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from src.application.runtime_lease_service import RuntimeLeaseService
from src.shared.errors import (
    ApplicationAlreadyRunningError,
    RuntimeLeaseLostError,
)


class FakeRuntimeLeaseRepository:
    """Small in-memory lease repository with expiration semantics."""

    def __init__(self) -> None:
        self.owner_id: str | None = None
        self.expires_at: datetime | None = None
        self.renew_result = True
        self.index_calls = 0

    async def ensure_indexes(self) -> None:
        """Record index preparation."""
        self.index_calls += 1

    async def try_acquire(
        self,
        lease_id: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
        metadata: dict[str, object],
    ) -> bool:
        """Acquire when absent, expired, or already owned."""
        del lease_id, metadata
        if (
            self.owner_id is not None
            and self.owner_id != owner_id
            and self.expires_at is not None
            and self.expires_at > now
        ):
            return False
        self.owner_id = owner_id
        self.expires_at = expires_at
        return True

    async def renew(
        self, lease_id: str, owner_id: str, expires_at: datetime
    ) -> bool:
        """Renew only for the current owner when scripted as successful."""
        del lease_id
        if not self.renew_result or self.owner_id != owner_id:
            return False
        self.expires_at = expires_at
        return True

    async def release(self, lease_id: str, owner_id: str) -> None:
        """Clear ownership for the current owner."""
        del lease_id
        if self.owner_id == owner_id:
            self.owner_id = None
            self.expires_at = None


async def test_second_runtime_instance_is_refused_until_release() -> None:
    """Only one owner may hold the same role identity."""
    repository = FakeRuntimeLeaseRepository()
    first = RuntimeLeaseService(
        repository, "bot-polling", ["secret-token"], owner_id="first"
    )
    second = RuntimeLeaseService(
        repository, "bot-polling", ["secret-token"], owner_id="second"
    )

    await first.acquire()
    with pytest.raises(ApplicationAlreadyRunningError):
        await second.acquire()

    await first.release()
    await second.acquire()
    assert second.is_acquired is True


async def test_expired_runtime_lease_can_be_acquired() -> None:
    """A crashed owner's expired lease must not block future startup."""
    repository = FakeRuntimeLeaseRepository()
    current = [datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)]
    first = RuntimeLeaseService(
        repository,
        "collector",
        ["session"],
        owner_id="first",
        lease_seconds=5,
        clock=lambda: current[0],
    )
    second = RuntimeLeaseService(
        repository,
        "collector",
        ["session"],
        owner_id="second",
        lease_seconds=5,
        clock=lambda: current[0],
    )
    await first.acquire()
    current[0] = current[0].replace(second=current[0].second + 6)

    await second.acquire()

    assert repository.owner_id == "second"


async def test_heartbeat_ownership_loss_cancels_guarded_component() -> None:
    """A lost lease stops the guarded worker instead of allowing duplicates."""
    repository = FakeRuntimeLeaseRepository()
    service = RuntimeLeaseService(
        repository,
        "bot-polling",
        ["token"],
        owner_id="owner",
        heartbeat_seconds=0.01,
    )
    await service.acquire()
    repository.renew_result = False
    cancelled = asyncio.Event()

    async def guarded() -> None:
        try:
            await asyncio.sleep(3600)
        finally:
            cancelled.set()

    with pytest.raises(RuntimeLeaseLostError):
        await service.run_with_heartbeat(guarded())

    assert cancelled.is_set()
    assert service.is_acquired is False


def test_lease_identifier_does_not_expose_secret() -> None:
    """Persisted lease ids contain only role and a one-way digest."""
    service = RuntimeLeaseService(
        FakeRuntimeLeaseRepository(),
        "bot-polling",
        ["very-secret-token"],
    )

    assert "very-secret-token" not in service.lease_id
    assert service.lease_id.startswith("bot-polling:")
