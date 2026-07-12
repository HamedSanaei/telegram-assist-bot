"""Verify authorization, explicit outcomes, and post-commit synchronization."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest

from telegram_assist_bot.application.scheduling import (
    CancelRequest,
    CancelScheduledPost,
)
from telegram_assist_bot.domain import CancellationPolicy, CancellationResult

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import ScheduleRepository

NOW = datetime(2026, 7, 12, tzinfo=UTC)


class Repository:
    """Return one scripted cancellation result."""

    def __init__(self, result: CancellationResult) -> None:
        self.result, self.calls = result, 0

    async def cancel(self, **_values: object) -> CancellationResult:
        self.calls += 1
        return self.result


def execute(
    repository: Repository,
    *,
    authorized: bool = True,
    synced: list[bool] | None = None,
    policy: CancellationPolicy = CancellationPolicy.PRESERVE,
) -> CancellationResult:
    async def synchronize() -> None:
        if synced is not None:
            synced.append(True)

    use_case = CancelScheduledPost(
        cast("ScheduleRepository", repository),
        clock=lambda: NOW,
        interval_seconds=300,
        policy=policy,
        synchronize=synchronize,
    )
    return asyncio.run(
        use_case.execute(CancelRequest("job", -1, 0, 42, "safe", authorized))
    )


def test_denies_unauthorized_actor_before_repository() -> None:
    repository = Repository(CancellationResult.CANCELLED)
    assert execute(repository, authorized=False) is CancellationResult.PERMISSION_DENIED
    assert repository.calls == 0


@pytest.mark.parametrize(
    "result",
    [
        CancellationResult.ALREADY_CANCELLED,
        CancellationResult.ALREADY_COMPLETED,
        CancellationResult.ALREADY_EXECUTING,
        CancellationResult.CONFLICT,
        CancellationResult.INVALID_STATE,
        CancellationResult.NOT_FOUND,
    ],
)
def test_returns_explicit_non_success_outcomes_without_sync(
    result: CancellationResult,
) -> None:
    synced: list[bool] = []
    assert execute(Repository(result), synced=synced) is result
    assert synced == []


@pytest.mark.parametrize("policy", list(CancellationPolicy))
def test_synchronizes_only_after_committed_cancellation(
    policy: CancellationPolicy,
) -> None:
    synced: list[bool] = []
    result = execute(
        Repository(CancellationResult.CANCELLED), synced=synced, policy=policy
    )
    assert result is CancellationResult.CANCELLED
    assert synced == [True]
