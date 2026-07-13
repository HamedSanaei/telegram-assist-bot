"""Verify one due-worker iteration and explicit outcome mapping."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from tests.unit.application.publication.test_publish_text_immediately import request

from telegram_assist_bot.application.publication import PublishResult, PublishStatus
from telegram_assist_bot.application.scheduling import RunDuePublication, RunDueStatus
from telegram_assist_bot.domain import ScheduledPublication

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports import ScheduleRepository

NOW = datetime(2026, 7, 12, tzinfo=UTC)


class Repository:
    """Script one due job and capture conditional transitions."""

    def __init__(
        self, job: ScheduledPublication | None, *, changed: bool = True
    ) -> None:
        self.job, self.changed = job, changed
        self.completed = self.deferred = self.failed = False

    async def claim_due(self, **_values: object) -> ScheduledPublication | None:
        value, self.job = self.job, None
        return value

    async def complete(self, *_args: object, **_kwargs: object) -> bool:
        self.completed = True
        return self.changed

    async def defer(self, *_args: object, **_kwargs: object) -> bool:
        self.deferred = True
        return self.changed

    async def fail(self, *_args: object, **_kwargs: object) -> bool:
        self.failed = True
        return self.changed


def runner(repository: Repository, status: PublishStatus) -> RunDuePublication:
    async def build(post_id: str, destination_id: int) -> object:
        return request(post_id=post_id, destination_id=destination_id)

    async def publish(_value: object) -> PublishResult:
        return PublishResult(status)

    return RunDuePublication(
        cast("ScheduleRepository", repository),
        owner="worker",
        clock=lambda: NOW,
        lease_seconds=30,
        max_attempts=3,
        retry_delay_seconds=2,
        build_request=build,  # type: ignore[arg-type]
        publish=publish,
    )


def job(*, attempts: int = 1) -> ScheduledPublication:
    return ScheduledPublication("job", "post", -1, NOW, attempt_count=attempts)


def test_idle_when_no_job_is_due() -> None:
    assert (
        asyncio.run(runner(Repository(None), PublishStatus.SUCCEEDED).execute_once())
        is RunDueStatus.IDLE
    )


@pytest.mark.parametrize(
    "status", [PublishStatus.SUCCEEDED, PublishStatus.ALREADY_PUBLISHED]
)
def test_completes_success_and_idempotent_success(status: PublishStatus) -> None:
    repository = Repository(job())
    assert (
        asyncio.run(runner(repository, status).execute_once()) is RunDueStatus.COMPLETED
    )
    assert repository.completed


def test_defers_retryable_result_below_attempt_cap() -> None:
    repository = Repository(job(attempts=2))
    assert (
        asyncio.run(runner(repository, PublishStatus.RETRY_PENDING).execute_once())
        is RunDueStatus.DEFERRED
    )
    assert repository.deferred


@pytest.mark.parametrize(
    "status", [PublishStatus.PERMANENT_FAILED, PublishStatus.OUTCOME_UNKNOWN]
)
def test_terminally_fails_permanent_and_unknown_results(status: PublishStatus) -> None:
    repository = Repository(job())
    assert asyncio.run(runner(repository, status).execute_once()) is RunDueStatus.FAILED
    assert repository.failed


def test_reports_lease_lost_without_claiming_success() -> None:
    repository = Repository(job(), changed=False)
    assert (
        asyncio.run(runner(repository, PublishStatus.SUCCEEDED).execute_once())
        is RunDueStatus.LEASE_LOST
    )


def test_cancellation_propagates_without_terminal_transition() -> None:
    repository = Repository(job())

    async def cancelled(_value: object) -> PublishResult:
        raise asyncio.CancelledError

    use_case = runner(repository, PublishStatus.SUCCEEDED)
    use_case._publish = cancelled
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(use_case.execute_once())
    assert not repository.completed
    assert not repository.failed


def test_action_filter_and_post_result_hook_are_explicit() -> None:
    async def scenario() -> None:
        repository = Repository(job())
        notified: list[RunDueStatus] = []
        use_case = runner(repository, PublishStatus.SUCCEEDED)
        use_case._action = "immediate"

        async def notify(value: ScheduledPublication, status: RunDueStatus) -> None:
            del value
            notified.append(status)

        use_case._after_result = notify
        assert await use_case.execute_once() is RunDueStatus.COMPLETED
        assert notified == [RunDueStatus.COMPLETED]

    asyncio.run(scenario())
    with pytest.raises(ValueError, match="action"):
        RunDuePublication(
            cast("ScheduleRepository", Repository(None)),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            max_attempts=3,
            retry_delay_seconds=2,
            action="invalid",
            build_request=cast("object", lambda: None),  # type: ignore[arg-type]
            publish=cast("object", lambda: None),  # type: ignore[arg-type]
        )


def test_invalid_worker_bounds_and_naive_clock_are_rejected() -> None:
    repository = cast("ScheduleRepository", Repository(job()))
    with pytest.raises(ValueError, match="configuration"):
        RunDuePublication(
            repository,
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=0,
            max_attempts=3,
            retry_delay_seconds=2,
            build_request=cast("object", lambda: None),  # type: ignore[arg-type]
            publish=cast("object", lambda: None),  # type: ignore[arg-type]
        )
    use_case = runner(Repository(job()), PublishStatus.SUCCEEDED)
    use_case._clock = lambda: NOW.replace(tzinfo=None)
    with pytest.raises(ValueError, match="aware"):
        asyncio.run(use_case.execute_once())
