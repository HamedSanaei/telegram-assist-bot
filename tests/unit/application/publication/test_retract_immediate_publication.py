"""Unit coverage for restart-safe immediate publication retraction."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from telegram_assist_bot.application.ports import PublisherError
from telegram_assist_bot.application.publication import (
    RetractImmediatePublication,
    RetractionStatus,
)
from telegram_assist_bot.domain import (
    Publication,
    PublicationFailureCategory,
    PublicationRetractionState,
    PublicationState,
)

NOW = datetime(2026, 7, 29, 10, tzinfo=UTC)


def receipt(*, attempts: int = 1) -> Publication:
    """Build one claimed deletion receipt."""
    return Publication(
        "publication",
        "post",
        -1001,
        PublicationState.SUCCEEDED,
        message_ids=(41, 42),
        published_at=NOW - timedelta(minutes=1),
        retraction_state=PublicationRetractionState.CLAIMED,
        retraction_owner="worker",
        retraction_attempt_count=attempts,
        retraction_selection_version=2,
    )


class Repository:
    """Record claim and terminal persistence decisions."""

    def __init__(self, publication: Publication | None) -> None:
        self.publication = publication
        self.failed: list[dict[str, Any]] = []
        self.completed = 0

    async def claim_retraction(self, **kwargs: object) -> Publication | None:
        del kwargs
        value, self.publication = self.publication, None
        return value

    async def complete_retraction(
        self, publication_id: str, *, owner: str, at: datetime
    ) -> Publication:
        assert publication_id == "publication"
        assert owner == "worker"
        assert at == NOW
        self.completed += 1
        return replace(
            receipt(),
            retraction_state=PublicationRetractionState.SUCCEEDED,
            retracted_at=at,
        )

    async def fail_retraction(
        self, publication_id: str, **kwargs: object
    ) -> Publication:
        assert publication_id == "publication"
        self.failed.append(dict(kwargs))
        state = (
            PublicationRetractionState.WAITING_FOR_RETRY
            if kwargs["next_attempt_at"] is not None
            else PublicationRetractionState.PERMANENT_FAILED
        )
        return replace(receipt(), retraction_state=state)


class Gateway:
    """Delete exact receipt values or raise one scripted error."""

    def __init__(self, error: PublisherError | None = None) -> None:
        self.error = error
        self.calls: list[tuple[int, tuple[int, ...], float]] = []

    async def delete(
        self,
        destination_id: int,
        message_ids: tuple[int, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        self.calls.append((destination_id, message_ids, timeout_seconds))
        if self.error is not None:
            raise self.error


def use_case(
    repository: Repository,
    gateway: Gateway,
    *,
    after: list[RetractionStatus] | None = None,
) -> RetractImmediatePublication:
    """Build deterministic test orchestration."""

    async def notify(_receipt: Publication, status: RetractionStatus) -> None:
        if after is not None:
            after.append(status)

    return RetractImmediatePublication(
        repository,  # type: ignore[arg-type]
        gateway,
        owner="worker",
        clock=lambda: NOW,
        timeout_seconds=10,
        lease_seconds=20,
        max_attempts=3,
        retry_delay_seconds=5,
        after_result=notify,
    )


def test_success_and_idle_are_idempotent() -> None:
    async def scenario() -> None:
        repository = Repository(receipt())
        gateway = Gateway()
        outcomes: list[RetractionStatus] = []
        service = use_case(repository, gateway, after=outcomes)
        assert await service.execute_once() is RetractionStatus.SUCCEEDED
        assert await service.execute_once() is RetractionStatus.IDLE
        assert gateway.calls == [(-1001, (41, 42), 10)]
        assert repository.completed == 1
        assert outcomes == [RetractionStatus.SUCCEEDED]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("category", "attempts", "expected", "retry"),
    [
        (
            PublicationFailureCategory.TIMEOUT,
            1,
            RetractionStatus.RETRY_PENDING,
            True,
        ),
        (
            PublicationFailureCategory.AMBIGUOUS,
            2,
            RetractionStatus.RETRY_PENDING,
            True,
        ),
        (
            PublicationFailureCategory.TRANSIENT,
            3,
            RetractionStatus.PERMANENT_FAILED,
            False,
        ),
        (
            PublicationFailureCategory.PERMISSION,
            1,
            RetractionStatus.PERMANENT_FAILED,
            False,
        ),
    ],
)
def test_failures_have_bounded_idempotent_retry(
    category: PublicationFailureCategory,
    attempts: int,
    expected: RetractionStatus,
    retry: bool,
) -> None:
    async def scenario() -> None:
        repository = Repository(receipt(attempts=attempts))
        gateway = Gateway(PublisherError(category, reason_code="safe"))
        assert await use_case(repository, gateway).execute_once() is expected
        assert len(repository.failed) == 1
        next_at = repository.failed[0]["next_attempt_at"]
        assert (next_at == NOW + timedelta(seconds=5)) is retry
        assert repository.failed[0]["failure_reason_code"] == "safe"

    asyncio.run(scenario())


def test_cancellation_propagates_without_persistence() -> None:
    class CancelledGateway(Gateway):
        async def delete(
            self,
            destination_id: int,
            message_ids: tuple[int, ...],
            *,
            timeout_seconds: float,
        ) -> None:
            del destination_id, message_ids, timeout_seconds
            raise asyncio.CancelledError

    async def scenario() -> None:
        repository = Repository(receipt())
        with pytest.raises(asyncio.CancelledError):
            await use_case(repository, CancelledGateway()).execute_once()
        assert repository.completed == 0
        assert repository.failed == []

    asyncio.run(scenario())


def test_unhandled_delete_failure_isolated_and_retried() -> None:
    class BrokenGateway(Gateway):
        async def delete(
            self,
            destination_id: int,
            message_ids: tuple[int, ...],
            *,
            timeout_seconds: float,
        ) -> None:
            del destination_id, message_ids, timeout_seconds
            raise RuntimeError("transport fixture")

    async def scenario() -> None:
        repository = Repository(receipt())
        outcomes: list[RetractionStatus] = []
        result = await use_case(
            repository, BrokenGateway(), after=outcomes
        ).execute_once()
        assert result is RetractionStatus.RETRY_PENDING
        assert repository.failed[0]["failure_reason_code"] == (
            "unhandled_delete_exception"
        )
        assert outcomes == [RetractionStatus.RETRY_PENDING]

    asyncio.run(scenario())


def test_configuration_and_clock_are_validated() -> None:
    with pytest.raises(ValueError, match="configuration"):
        RetractImmediatePublication(
            Repository(None),  # type: ignore[arg-type]
            Gateway(),
            owner="worker",
            clock=lambda: NOW,
            timeout_seconds=10,
            lease_seconds=5,
            max_attempts=3,
            retry_delay_seconds=1,
        )

    async def scenario() -> None:
        service = RetractImmediatePublication(
            Repository(receipt()),  # type: ignore[arg-type]
            Gateway(),
            owner="worker",
            clock=lambda: NOW.replace(tzinfo=None),
            timeout_seconds=1,
            lease_seconds=1,
            max_attempts=1,
            retry_delay_seconds=1,
        )
        with pytest.raises(ValueError, match="aware"):
            await service.execute_once()

    asyncio.run(scenario())
