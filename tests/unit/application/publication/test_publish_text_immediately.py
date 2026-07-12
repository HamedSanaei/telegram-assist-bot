"""Verify immediate text publication guards, fidelity, and failure mapping."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from telegram_assist_bot.application.ports import (
    PublicationClaimOutcome,
    PublicationClaimResult,
    PublicationPayload,
    PublisherError,
)
from telegram_assist_bot.application.publication import (
    PublishImmediately,
    PublishRequest,
    PublishStatus,
)
from telegram_assist_bot.domain import (
    Publication,
    PublicationFailureCategory,
    PublicationState,
    PublishedMessage,
)
from telegram_assist_bot.domain.posts import TelegramEntity

NOW = datetime(2026, 7, 12, 12, tzinfo=UTC)


def async_test(function: object) -> object:
    """Run one typed async test without an event-loop plugin."""
    import functools

    @functools.wraps(function)  # type: ignore[arg-type]
    def wrapper(*args: object, **kwargs: object) -> object:
        return asyncio.run(function(*args, **kwargs))  # type: ignore[operator]

    return wrapper


class Repository:
    """Keep canonical state for deterministic use-case tests."""

    def __init__(self) -> None:
        self.value: Publication | None = None

    async def claim(self, **values: object) -> PublicationClaimResult:
        identity = str(values["publication_id"])
        if self.value and self.value.state in {
            PublicationState.SUCCEEDED,
            PublicationState.PERMANENT_FAILED,
            PublicationState.OUTCOME_UNKNOWN,
        }:
            return PublicationClaimResult(PublicationClaimOutcome.TERMINAL, self.value)
        attempt = 1 if self.value is None else self.value.attempt_count + 1
        destination_id = values["destination_id"]
        assert type(destination_id) is int
        self.value = Publication(
            identity,
            str(values["post_id"]),
            destination_id,
            PublicationState.CLAIMED,
            attempt_count=attempt,
            claim_owner=str(values["owner"]),
            attempted_at=NOW,
        )
        return PublicationClaimResult(PublicationClaimOutcome.CLAIMED, self.value)

    async def complete(
        self, _identity: str, *, owner: str, result: PublishedMessage
    ) -> Publication:
        assert self.value is not None
        assert self.value.claim_owner == owner
        self.value = replace(
            self.value,
            state=PublicationState.SUCCEEDED,
            message_ids=result.message_ids,
            published_at=result.published_at,
            claim_owner=None,
        )
        return self.value

    async def fail(
        self,
        _identity: str,
        *,
        owner: str,
        category: PublicationFailureCategory,
        now: datetime,
        next_attempt_at: datetime | None,
        outcome_unknown: bool,
    ) -> Publication:
        del owner, now
        assert self.value
        state = (
            PublicationState.OUTCOME_UNKNOWN
            if outcome_unknown
            else (
                PublicationState.WAITING_FOR_RETRY
                if next_attempt_at
                else PublicationState.PERMANENT_FAILED
            )
        )
        self.value = replace(self.value, state=state, error_category=category.value)
        return self.value


class Publisher:
    """Capture exact payloads and scripted errors."""

    def __init__(self, errors: list[PublisherError] | None = None) -> None:
        self.errors = errors or []
        self.payloads: list[PublicationPayload] = []

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        assert timeout_seconds == 10
        self.payloads.append(payload)
        if self.errors:
            raise self.errors.pop(0)
        return PublishedMessage((91,), NOW)


def request(**changes: object) -> PublishRequest:
    entity = TelegramEntity(5, 2, "custom_emoji", "987654")
    base: dict[str, object] = {
        "post_id": "post-1",
        "destination_id": -1002,
        "payload": PublicationPayload(
            -1002,
            "سلام\u200cدنیا\n🙂",  # noqa: RUF001
            (entity,),
        ),
        "owner": "worker-a",
        "correlation_id": "correlation-safe",
        "authorized": True,
        "post_publishable": True,
        "immediate_selected": True,
        "session_valid": True,
        "account_premium": True,
        "destination_accessible": True,
    }
    base.update(changes)
    return PublishRequest(**base)  # type: ignore[arg-type]


def service(
    repository: Repository,
    publisher: Publisher,
    sleeps: list[float] | None = None,
) -> PublishImmediately:
    async def sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    return PublishImmediately(
        repository,
        publisher,
        clock=lambda: NOW,
        sleeper=sleep,
        timeout_seconds=10,
        lease_seconds=20,
        max_attempts=3,
    )


@async_test
async def test_publishes_exact_prepared_persian_text_and_entities() -> None:
    repository, publisher = Repository(), Publisher()
    result = await service(repository, publisher).execute(request())
    assert result.status is PublishStatus.SUCCEEDED
    assert result.publication is not None
    assert result.publication.message_ids == (91,)
    assert publisher.payloads[0].text == "سلام\u200cدنیا\n🙂"  # noqa: RUF001
    assert publisher.payloads[0].entities[0].custom_emoji_id == "987654"


@async_test
@pytest.mark.parametrize(
    "field",
    [
        "authorized",
        "post_publishable",
        "immediate_selected",
        "session_valid",
        "account_premium",
        "destination_accessible",
    ],
)
async def test_rejects_each_invalid_guard_before_external_call(field: str) -> None:
    repository, publisher = Repository(), Publisher()
    result = await service(repository, publisher).execute(request(**{field: False}))
    assert result.status is PublishStatus.REJECTED
    assert publisher.payloads == []


@async_test
async def test_returns_prior_success_without_second_send() -> None:
    repository, publisher = Repository(), Publisher()
    use_case = service(repository, publisher)
    await use_case.execute(request())
    result = await use_case.execute(request())
    assert result.status is PublishStatus.ALREADY_PUBLISHED
    assert len(publisher.payloads) == 1


@async_test
async def test_retries_only_certain_pre_send_transient_failure() -> None:
    repository = Repository()
    publisher = Publisher([PublisherError(PublicationFailureCategory.TRANSIENT)])
    sleeps: list[float] = []
    result = await service(repository, publisher, sleeps).execute(request())
    assert result.status is PublishStatus.SUCCEEDED
    assert len(publisher.payloads) == 2
    assert sleeps == [1.0]


@async_test
@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            PublisherError(PublicationFailureCategory.PERMISSION),
            PublishStatus.PERMANENT_FAILED,
        ),
        (
            PublisherError(
                PublicationFailureCategory.TIMEOUT,
                request_may_have_reached_telegram=True,
            ),
            PublishStatus.OUTCOME_UNKNOWN,
        ),
    ],
)
async def test_does_not_retry_permanent_or_ambiguous_failure(
    error: PublisherError, expected: PublishStatus
) -> None:
    repository, publisher = Repository(), Publisher([error])
    result = await service(repository, publisher).execute(request())
    assert result.status is expected
    assert len(publisher.payloads) == 1
