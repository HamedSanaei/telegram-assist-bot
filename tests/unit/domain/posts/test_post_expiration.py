from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from typing import cast
from zoneinfo import ZoneInfo

import pytest

from telegram_assist_bot.domain.posts import (
    POST_RETENTION_PERIOD,
    InvalidPostTransitionError,
    NaiveDatetimeError,
    OriginalPostContent,
    Post,
    PostId,
    PostInvariantError,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)


def _make_post(
    *,
    received_at: datetime,
    source_published_at: datetime | None = None,
) -> Post:
    return Post(
        post_id=PostId("post-expiration-1"),
        source_identity=SourceMessageIdentity(-1009876543210, 75),
        source_channel_username=None,
        source_channel_display_name="Source channel",
        original_content=OriginalPostContent(
            text="Original text",
            caption=None,
        ),
        source_published_at=source_published_at or received_at,
        received_at=received_at,
    )


@pytest.mark.parametrize(
    ("received_at", "expected_expiration"),
    [
        (
            datetime(2026, 1, 25, 20, 15, tzinfo=UTC),
            datetime(2026, 2, 8, 20, 15, tzinfo=UTC),
        ),
        (
            datetime(2025, 12, 25, 20, 15, tzinfo=UTC),
            datetime(2026, 1, 8, 20, 15, tzinfo=UTC),
        ),
        (
            datetime(2024, 2, 23, 20, 15, tzinfo=UTC),
            datetime(2024, 3, 8, 20, 15, tzinfo=UTC),
        ),
    ],
)
def test_expiration_is_exactly_fourteen_days_across_calendar_boundaries(
    received_at: datetime,
    expected_expiration: datetime,
) -> None:
    post = _make_post(received_at=received_at)

    assert timedelta(days=14) == POST_RETENTION_PERIOD
    assert post.expires_at == expected_expiration
    assert post.expires_at - post.received_at == timedelta(days=14)


def test_aware_non_utc_timestamps_are_canonicalized_to_utc() -> None:
    iran_offset = timezone(timedelta(hours=3, minutes=30))
    source_published_at = datetime(2026, 5, 3, 10, 15, tzinfo=iran_offset)
    received_at = datetime(2026, 5, 3, 10, 30, tzinfo=iran_offset)

    post = _make_post(
        received_at=received_at,
        source_published_at=source_published_at,
    )

    assert post.source_published_at == datetime(2026, 5, 3, 6, 45, tzinfo=UTC)
    assert post.received_at == datetime(2026, 5, 3, 7, 0, tzinfo=UTC)
    assert post.expires_at == datetime(2026, 5, 17, 7, 0, tzinfo=UTC)
    assert post.source_published_at.tzinfo is UTC
    assert post.received_at.tzinfo is UTC
    assert post.expires_at.tzinfo is UTC


def test_expiration_is_elapsed_time_not_local_wall_time_across_dst() -> None:
    new_york = ZoneInfo("America/New_York")
    received_at = datetime(2026, 3, 1, 12, 0, tzinfo=new_york)

    post = _make_post(received_at=received_at)

    assert post.received_at == datetime(2026, 3, 1, 17, 0, tzinfo=UTC)
    assert post.expires_at == datetime(2026, 3, 15, 17, 0, tzinfo=UTC)
    assert post.expires_at - post.received_at == timedelta(days=14)
    assert post.expires_at.astimezone(new_york) == datetime(
        2026, 3, 15, 13, 0, tzinfo=new_york
    )


@pytest.mark.parametrize(
    ("source_published_at", "received_at"),
    [
        (
            datetime(2026, 1, 1, 9, 0),  # noqa: DTZ001 - intentional naive input
            datetime(2026, 1, 1, 9, 1, tzinfo=UTC),
        ),
        (
            datetime(2026, 1, 1, 9, 0, tzinfo=UTC),
            datetime(2026, 1, 1, 9, 1),  # noqa: DTZ001 - intentional naive input
        ),
    ],
)
def test_post_rejects_naive_source_or_received_timestamp(
    source_published_at: datetime,
    received_at: datetime,
) -> None:
    with pytest.raises(NaiveDatetimeError):
        _make_post(
            received_at=received_at,
            source_published_at=source_published_at,
        )


def test_post_rejects_a_non_datetime_timestamp_with_domain_error() -> None:
    with pytest.raises(PostInvariantError):
        _make_post(
            received_at=cast("datetime", "2026-01-01T00:00:00Z"),
        )


def test_post_wraps_utc_conversion_overflow_as_domain_error() -> None:
    underflowing_source_time = datetime.min.replace(
        tzinfo=timezone(timedelta(hours=14))
    )

    with pytest.raises(PostInvariantError):
        _make_post(
            received_at=datetime(2026, 1, 1, tzinfo=UTC),
            source_published_at=underflowing_source_time,
        )


def test_post_wraps_expiration_overflow_as_domain_error() -> None:
    with pytest.raises(PostInvariantError):
        _make_post(received_at=datetime.max.replace(tzinfo=UTC))


def test_expiration_predicate_changes_at_exact_boundary() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    assert not post.is_expired_at(post.expires_at - timedelta(microseconds=1))
    assert post.is_expired_at(post.expires_at)
    assert post.is_expired_at(post.expires_at + timedelta(microseconds=1))


def test_expiration_predicate_normalizes_other_aware_timezone() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))
    plus_four = timezone(timedelta(hours=4))
    same_instant = post.expires_at.astimezone(plus_four)

    assert post.is_expired_at(same_instant)


def test_expiration_predicate_rejects_naive_timestamp() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(NaiveDatetimeError):
        post.is_expired_at(
            datetime(2026, 1, 15)  # noqa: DTZ001 - intentional naive input
        )


def test_stored_transition_is_allowed_until_just_before_expiration() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=post.expires_at - timedelta(microseconds=1),
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    assert stored.status is PostStatus.STORED


def test_stored_transition_is_rejected_at_expiration_boundary() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(InvalidPostTransitionError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.expires_at,
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
        )


def test_expired_transition_is_rejected_just_before_boundary() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(InvalidPostTransitionError):
        post.transition_to(
            PostStatus.EXPIRED,
            expected_version=0,
            occurred_at=post.expires_at - timedelta(microseconds=1),
            actor_category=TransitionActorCategory.SERVICE,
            reason="retention elapsed",
        )


def test_expired_transition_is_allowed_at_and_after_boundary() -> None:
    for delta in (timedelta(0), timedelta(seconds=1)):
        post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

        expired = post.transition_to(
            PostStatus.EXPIRED,
            expected_version=0,
            occurred_at=post.expires_at + delta,
            actor_category=TransitionActorCategory.SERVICE,
            reason="retention elapsed",
        )

        assert expired.status is PostStatus.EXPIRED
        assert expired.transition_history[-1].occurred_at == post.expires_at + delta


def test_stored_post_can_expire_at_exact_boundary() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))
    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=post.received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    expired = stored.transition_to(
        PostStatus.EXPIRED,
        expected_version=1,
        occurred_at=stored.expires_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="retention elapsed",
    )

    assert expired.status is PostStatus.EXPIRED
    assert expired.version == 2


def test_transition_rejects_time_before_receipt() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(InvalidPostTransitionError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=post.received_at - timedelta(microseconds=1),
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
        )


def test_transition_rejects_naive_timestamp() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))

    with pytest.raises(NaiveDatetimeError):
        post.transition_to(
            PostStatus.STORED,
            expected_version=0,
            occurred_at=datetime(  # noqa: DTZ001 - intentional naive input
                2026,
                1,
                1,
            ),
            actor_category=TransitionActorCategory.SERVICE,
            reason="persisted",
        )


def test_transition_timestamp_is_canonicalized_to_utc() -> None:
    post = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))
    plus_four = timezone(timedelta(hours=4))
    occurred_at = datetime(2026, 1, 1, 4, 30, tzinfo=plus_four)

    stored = post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=occurred_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted",
    )

    assert stored.transition_history[-1].occurred_at == datetime(
        2026, 1, 1, 0, 30, tzinfo=UTC
    )
    assert stored.transition_history[-1].occurred_at.tzinfo is UTC


def test_rehydration_recomputes_expiration_from_canonical_received_time() -> None:
    initial = _make_post(received_at=datetime(2026, 1, 1, tzinfo=UTC))
    plus_four = timezone(timedelta(hours=4))

    rehydrated = replace(
        initial,
        received_at=datetime(2026, 2, 1, 4, 0, tzinfo=plus_four),
    )

    assert rehydrated.received_at == datetime(2026, 2, 1, tzinfo=UTC)
    assert rehydrated.expires_at == datetime(2026, 2, 15, tzinfo=UTC)
