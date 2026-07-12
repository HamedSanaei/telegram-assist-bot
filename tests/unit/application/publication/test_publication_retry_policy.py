"""Verify deterministic identities, bounded retry, and terminal outcomes."""

from __future__ import annotations

import asyncio

from tests.unit.application.publication.test_publish_text_immediately import (
    NOW,
    Publisher,
    Repository,
    request,
    service,
)

from telegram_assist_bot.application.ports import PublisherError
from telegram_assist_bot.application.publication import (
    PublishImmediately,
    PublishStatus,
)
from telegram_assist_bot.domain import PublicationFailureCategory, publication_identity


def test_identity_is_stable_and_destination_scoped() -> None:
    first = publication_identity("post", -1001)
    assert first == publication_identity("post", -1001)
    assert first != publication_identity("post", -1002)
    assert first != publication_identity("other", -1001)
    assert first != publication_identity("post", -1001, "scheduled")


def test_bounded_exponential_retry_stops_at_max_attempts() -> None:
    errors = [PublisherError(PublicationFailureCategory.TRANSIENT) for _ in range(3)]
    sleeps: list[float] = []
    repository, publisher = Repository(), Publisher(errors)
    result = asyncio.run(service(repository, publisher, sleeps).execute(request()))
    assert result.status is PublishStatus.PERMANENT_FAILED
    assert len(publisher.payloads) == 3
    assert sleeps == [1.0, 2.0]


def test_ambiguous_attempt_is_terminal_without_retry() -> None:
    publisher = Publisher(
        [
            PublisherError(
                PublicationFailureCategory.AMBIGUOUS,
                request_may_have_reached_telegram=True,
            )
        ]
    )
    result = asyncio.run(service(Repository(), publisher).execute(request()))
    assert result.status is PublishStatus.OUTCOME_UNKNOWN
    assert len(publisher.payloads) == 1


def test_injected_jitter_is_deterministic_and_bounded() -> None:
    """Exercise the low edge of the configured twenty-percent jitter band."""
    sleeps: list[float] = []

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    use_case = PublishImmediately(
        Repository(),
        Publisher([PublisherError(PublicationFailureCategory.TRANSIENT)]),
        clock=lambda: NOW,
        sleeper=sleep,
        timeout_seconds=10,
        lease_seconds=20,
        max_attempts=3,
        jitter_source=lambda: 0.0,
        jitter_ratio=0.2,
    )
    assert asyncio.run(use_case.execute(request())).status is PublishStatus.SUCCEEDED
    assert sleeps == [0.8]
