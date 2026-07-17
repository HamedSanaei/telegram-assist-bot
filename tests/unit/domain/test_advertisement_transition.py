"""Unit tests for advertisement state independent from Post lifecycle."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_assist_bot.domain.advertisement import (
    AdvertisementCheckFailure,
    AdvertisementCheckResult,
    AdvertisementFailurePolicy,
    AdvertisementProcessingState,
    InvalidAdvertisementResultError,
    InvalidAdvertisementTransitionError,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)

_NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def _stored_post() -> Post:
    post = Post(
        post_id=PostId("post-ad-1"),
        source_identity=SourceMessageIdentity(-1001, 42),
        source_channel_username="source",
        source_channel_display_name="منبع",
        original_content=OriginalPostContent("خبر با نیم‌فاصله و Emoji 🚀", None),
        source_published_at=_NOW - timedelta(minutes=1),
        received_at=_NOW,
    )
    return post.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )


def _result(
    *, is_advertisement: bool, confidence: float = 0.5
) -> AdvertisementCheckResult:
    return AdvertisementCheckResult(
        is_advertisement=is_advertisement,
        confidence=confidence,
        reason="دلیل فارسی با نیم‌فاصله و Emoji ✨",
        provider_name="provider",
        model_name="model",
        checked_at=_NOW + timedelta(seconds=2),
        prompt_version="1.0.0",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
    )


def _pending() -> Post:
    return _stored_post().start_advertisement_check(
        job_id="job-ad-1",
        expected_processing_version=0,
        requested_at=_NOW + timedelta(seconds=1),
    )


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_result_confidence_boundaries_and_persian_are_preserved(
    confidence: float,
) -> None:
    result = _result(is_advertisement=False, confidence=confidence)

    assert result.confidence == confidence
    assert result.reason == "دلیل فارسی با نیم‌فاصله و Emoji ✨"


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_result_rejects_confidence_outside_strict_schema_range(
    confidence: float,
) -> None:
    with pytest.raises(InvalidAdvertisementResultError):
        _result(is_advertisement=False, confidence=confidence)


def test_advertising_result_rejects_post_and_ignores_confidence_thresholds() -> None:
    pending = _pending()
    target = pending.apply_advertisement_result(
        _result(is_advertisement=True, confidence=0.01),
        job_id="job-ad-1",
        expected_processing_version=1,
    )

    assert target.advertisement_state is (
        AdvertisementProcessingState.REJECTED_AS_ADVERTISEMENT
    )
    assert not target.advertisement_allows_next_stage


def test_non_advertising_result_advances_exactly_one_processing_step() -> None:
    pending = _pending()
    target = pending.apply_advertisement_result(
        _result(is_advertisement=False, confidence=0.01),
        job_id="job-ad-1",
        expected_processing_version=1,
    )

    assert target.advertisement_state is AdvertisementProcessingState.PASSED
    assert target.advertisement_processing_version == 2
    assert target.advertisement_allows_next_stage
    with pytest.raises(InvalidAdvertisementTransitionError):
        target.apply_advertisement_result(
            _result(is_advertisement=True),
            job_id="job-ad-1",
            expected_processing_version=2,
        )


@pytest.mark.parametrize(
    ("policy", "state", "allows_next", "manual"),
    [
        (
            AdvertisementFailurePolicy.CONTINUE_PROCESSING,
            AdvertisementProcessingState.FAILED_CONTINUE,
            True,
            False,
        ),
        (
            AdvertisementFailurePolicy.STOP_PROCESSING,
            AdvertisementProcessingState.PROCESSING_STOPPED,
            False,
            False,
        ),
        (
            AdvertisementFailurePolicy.MANUAL_REVIEW,
            AdvertisementProcessingState.MANUAL_REVIEW_REQUIRED,
            False,
            True,
        ),
    ],
)
def test_terminal_failure_policy_transitions(
    policy: AdvertisementFailurePolicy,
    state: AdvertisementProcessingState,
    allows_next: bool,
    manual: bool,
) -> None:
    pending = _pending()
    failure = AdvertisementCheckFailure(
        policy=policy,
        failure_category="transient",
        failure_type="all_providers_failed",
        failed_at=_NOW + timedelta(seconds=2),
        attempted_candidates_count=2,
        retry_count=1,
        fallback_count=1,
    )

    target = pending.apply_advertisement_failure(
        failure,
        job_id="job-ad-1",
        expected_processing_version=1,
    )

    assert target.advertisement_state is state
    assert target.advertisement_allows_next_stage is allows_next
    assert target.advertisement_requires_manual_review is manual
    assert target.advertisement_manual_review_reason == (
        "advertisement_check_failed" if manual else None
    )
    assert target.advertisement_result is None


def test_retry_later_requires_existing_future_job_schedule() -> None:
    pending = _pending()
    failure = AdvertisementCheckFailure(
        policy=AdvertisementFailurePolicy.RETRY_LATER,
        failure_category="timeout",
        failure_type="all_providers_failed",
        failed_at=_NOW + timedelta(seconds=2),
        attempted_candidates_count=1,
        retry_count=1,
        fallback_count=0,
        next_retry_at=_NOW + timedelta(seconds=32),
    )

    target = pending.apply_advertisement_failure(
        failure,
        job_id="job-ad-1",
        expected_processing_version=1,
    )

    assert target.advertisement_state is AdvertisementProcessingState.RETRY_PENDING
    assert target.advertisement_failure is failure


def test_expired_or_stale_post_cannot_move_backward() -> None:
    pending = _pending()
    with pytest.raises(InvalidAdvertisementTransitionError):
        pending.apply_advertisement_result(
            _result(is_advertisement=False),
            job_id="job-ad-1",
            expected_processing_version=0,
        )

    expired_result = AdvertisementCheckResult(
        is_advertisement=False,
        confidence=0.5,
        reason="نتیجه دیرهنگام",
        provider_name="provider",
        model_name="model",
        checked_at=pending.expires_at,
        prompt_version="1.0.0",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
    )
    with pytest.raises(InvalidAdvertisementTransitionError):
        pending.apply_advertisement_result(
            expired_result,
            job_id="job-ad-1",
            expected_processing_version=1,
        )
