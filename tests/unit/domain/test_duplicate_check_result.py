"""Domain tests for exact-compatible semantic duplicate states."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_assist_bot.domain.advertisement import AdvertisementCheckResult
from telegram_assist_bot.domain.duplicates import (
    SemanticDuplicatePolicy,
    SemanticDuplicateResult,
    SemanticDuplicateState,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)

_NOW = datetime(2026, 7, 18, 12, tzinfo=UTC)


def _pending() -> Post:
    post = Post(
        PostId("semantic-current"),
        SourceMessageIdentity(-1001, 1),
        "source",
        "منبع",
        OriginalPostContent("متن فارسی با نیم‌فاصله و Emoji 🚀", None),
        _NOW - timedelta(minutes=2),
        _NOW - timedelta(minutes=1),
    ).transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=_NOW,
        actor_category=TransitionActorCategory.SERVICE,
        reason="stored",
    )
    advertisement = AdvertisementCheckResult(
        False,
        0.8,
        "تبلیغ نیست",
        "provider",
        "model",
        _NOW,
        "1.0.0",
        "1",
        1,
        0,
    )
    post = post.start_advertisement_check(
        job_id="ad-job", expected_processing_version=0, requested_at=_NOW
    ).apply_advertisement_result(
        advertisement, job_id="ad-job", expected_processing_version=1
    )
    return post.start_semantic_duplicate_check(
        job_id="semantic-job", expected_processing_version=0, requested_at=_NOW
    )


def _result(*, duplicate: bool, similarity: float = 0.88) -> SemanticDuplicateResult:
    return SemanticDuplicateResult(
        is_duplicate=duplicate,
        similarity=similarity,
        confidence=0.6,
        matched_post_id=PostId("candidate") if duplicate else None,
        reason="دلیل فارسی با نیم‌فاصله ✨",
        provider_name="provider",
        model_name="model",
        checked_at=_NOW,
        prompt_version="2.0.0",
        schema_version="2",
        attempt_number=1,
        fallback_count=0,
    )


@pytest.mark.parametrize(
    ("policy", "state", "next_stage", "manual"),
    [
        (
            SemanticDuplicatePolicy.REJECT,
            SemanticDuplicateState.DUPLICATE_REJECTED,
            False,
            False,
        ),
        (
            SemanticDuplicatePolicy.MANUAL_REVIEW,
            SemanticDuplicateState.DUPLICATE_MANUAL_REVIEW,
            False,
            True,
        ),
        (
            SemanticDuplicatePolicy.CONTINUE_PROCESSING,
            SemanticDuplicateState.DUPLICATE_ALLOWED,
            True,
            False,
        ),
    ],
)
def test_duplicate_policies_preserve_known_duplicate_metadata(
    policy: SemanticDuplicatePolicy,
    state: SemanticDuplicateState,
    next_stage: bool,
    manual: bool,
) -> None:
    target = _pending().apply_semantic_duplicate_result(
        _result(duplicate=True),
        policy=policy,
        job_id="semantic-job",
        expected_processing_version=1,
    )
    assert target.semantic_duplicate_state is state
    assert target.semantic_duplicate_allows_next_stage is next_stage
    assert target.semantic_duplicate_requires_manual_review is manual
    assert target.semantic_duplicate_result is not None
    assert target.semantic_duplicate_result.is_duplicate
    assert target.semantic_duplicate_manual_review_reason == (
        "semantic_duplicate_detected" if manual else None
    )


def test_non_duplicate_has_no_matched_identity_and_advances() -> None:
    target = _pending().apply_semantic_duplicate_result(
        _result(duplicate=False, similarity=0.879999),
        policy=SemanticDuplicatePolicy.REJECT,
        job_id="semantic-job",
        expected_processing_version=1,
    )
    assert target.semantic_duplicate_state is SemanticDuplicateState.PASSED
    assert target.semantic_duplicate_allows_next_stage
    assert target.semantic_duplicate_result is not None
    assert target.semantic_duplicate_result.matched_post_id is None


@pytest.mark.parametrize("similarity", [-0.01, 1.01])
def test_similarity_is_strictly_bounded(similarity: float) -> None:
    with pytest.raises(ValueError, match="similarity"):
        _result(duplicate=True, similarity=similarity)


def test_exact_contract_stays_independent_from_semantic_result() -> None:
    pending = _pending()
    assert pending.semantic_duplicate_result is None
    assert pending.original_text == "متن فارسی با نیم‌فاصله و Emoji 🚀"
