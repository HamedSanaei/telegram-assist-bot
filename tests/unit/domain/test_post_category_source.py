"""Unit tests for Post categorization transitions and active category state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_assist_bot.domain.categories import (
    CategorizationCheckFailure,
    CategorizationMethod,
    CategorizationResult,
    CategorizationState,
    Category,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostInvariantError,
    PostStatus,
    PostVersionConflictError,
    SourceMessageIdentity,
    TransitionActorCategory,
)

_NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def _stored_post() -> Post:
    post = Post(
        post_id=PostId("post-cat-1"),
        source_identity=SourceMessageIdentity(-1002, 43),
        source_channel_username="source",
        source_channel_display_name="منبع",
        original_content=OriginalPostContent("متن دسته‌بندی موضوعی با نیم‌فاصله", None),
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


def test_category_active_state() -> None:
    """Verify active attribute on Category domain object."""
    cat_default = Category("news", "اخبار")
    assert cat_default.active is True

    cat_inactive = Category("tech", "فناوری", active=False)
    assert cat_inactive.active is False


def test_post_categorization_defaults() -> None:
    """Verify Post initializes with default categorization NOT_REQUESTED state."""
    post = _stored_post()
    assert post.categorization_state is CategorizationState.NOT_REQUESTED
    assert post.categorization_processing_version == 0
    assert post.categorization_job_id is None
    assert post.categorization_result is None
    assert post.categorization_failure is None


def test_enqueue_categorization() -> None:
    """Verify Post correctly transitions to pending categorization state."""
    post = _stored_post()
    job_id = "job-cat-abc"
    updated = post.enqueue_categorization(job_id)

    assert updated.categorization_state is CategorizationState.PENDING
    assert updated.categorization_job_id == job_id
    assert updated.categorization_processing_version == 1


def test_enqueue_categorization_idempotent() -> None:
    """Verify enqueuing with same job ID is idempotent."""
    post = _stored_post()
    job_id = "job-cat-abc"
    first = post.enqueue_categorization(job_id)
    second = first.enqueue_categorization(job_id)

    assert first is second


def test_enqueue_categorization_invalid_job_id() -> None:
    """Verify invalid job ID raises error."""
    post = _stored_post()
    with pytest.raises(PostInvariantError):
        post.enqueue_categorization("")


def test_apply_categorization_result_success() -> None:
    """Verify applying AI categorization result transitions to AI_ASSIGNED state."""
    post = _stored_post()
    job_id = "job-cat-abc"
    pending = post.enqueue_categorization(job_id)

    result = CategorizationResult(
        category_id="news",
        method=CategorizationMethod.AI,
        policy_version=2,
        assigned_at=_NOW,
        confidence=0.9,
        reason="reason text",
        provider_name="deepseek",
        model_name="deepseek-v4-flash",
        prompt_version="2.0.0",
        schema_version="2",
    )

    applied = pending.apply_categorization_result(
        result,
        job_id=job_id,
        expected_processing_version=1,
    )

    assert applied.categorization_state is CategorizationState.AI_ASSIGNED
    assert applied.categorization_result == result
    assert applied.categorization_processing_version == 2


def test_apply_categorization_result_version_conflict() -> None:
    """Verify version mismatch raises error."""
    post = _stored_post()
    pending = post.enqueue_categorization("job-cat-abc")

    result = CategorizationResult(
        category_id="news",
        method=CategorizationMethod.AI,
        policy_version=2,
        assigned_at=_NOW,
        confidence=0.8,
        provider_name="provider-a",
        model_name="model-a",
        prompt_version="2.0.0",
        schema_version="2",
    )

    with pytest.raises(PostVersionConflictError):
        pending.apply_categorization_result(
            result, "job-cat-abc", expected_processing_version=0
        )


def test_apply_categorization_failure_retry() -> None:
    """Verify applying failure with retry transitions to RETRY_PENDING state."""
    post = _stored_post()
    job_id = "job-cat-abc"
    pending = post.enqueue_categorization(job_id)

    failure = CategorizationCheckFailure(
        policy="retry_later",
        failure_category="rate_limit",
        failed_at=_NOW,
        next_retry_at=_NOW + timedelta(minutes=1),
    )

    updated = pending.apply_categorization_failure(
        failure,
        job_id=job_id,
        expected_processing_version=1,
    )

    assert updated.categorization_state is CategorizationState.RETRY_PENDING
    assert updated.categorization_failure == failure
    assert updated.categorization_processing_version == 2


def test_manual_result_cannot_be_overwritten_by_ai() -> None:
    """A stale AI result must preserve an already committed manual assignment."""
    post = _stored_post().enqueue_categorization("job-cat")
    manual = CategorizationResult(
        category_id="manual",
        method=CategorizationMethod.MANUAL,
        policy_version=1,
        assigned_at=_NOW,
    )
    manually_assigned = post.apply_categorization_result(
        manual,
        job_id="job-cat",
        expected_processing_version=post.categorization_processing_version,
    )
    ai_result = CategorizationResult(
        category_id="ai-category",
        method=CategorizationMethod.AI,
        policy_version=2,
        assigned_at=_NOW,
        reason="نتیجه",
        confidence=0.9,
        provider_name="provider-a",
        model_name="model-a",
        prompt_version="2.0.0",
        schema_version="2",
    )

    unchanged = manually_assigned.apply_categorization_result(
        ai_result,
        job_id="job-cat",
        expected_processing_version=(
            manually_assigned.categorization_processing_version
        ),
    )

    assert unchanged is manually_assigned
    assert unchanged.categorization_result == manual
