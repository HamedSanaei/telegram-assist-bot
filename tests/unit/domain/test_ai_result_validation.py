# ruff: noqa: DTZ001, PT011
# mypy: disable-error-code="arg-type"
"""Fail-closed tests for provider-independent AI result metadata."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from telegram_assist_bot.domain.ai.provider_health import (
    ActiveReservation,
    ProviderAttemptOutcome,
    ProviderFailureCategory,
    ProviderHealth,
    ReservationKind,
)
from telegram_assist_bot.domain.duplicates import (
    DuplicateCheckResult,
    SemanticDuplicateFailure,
    SemanticDuplicateFailurePolicy,
    SemanticDuplicateResult,
)
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.domain.scoring import (
    ScoringFailure,
    ScoringFailurePolicy,
    ScoringResult,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)


def _semantic() -> SemanticDuplicateResult:
    return SemanticDuplicateResult(
        False,
        0.2,
        0.8,
        None,
        "different",
        "provider",
        "model",
        NOW,
        "1",
        "1",
        1,
        0,
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("is_duplicate", 1),
        ("similarity", 1),
        ("similarity", 1.1),
        ("confidence", 1),
        ("confidence", -0.1),
        ("matched_post_id", PostId("candidate")),
        ("method", "exact"),
        ("reason", " "),
        ("reason", "bad\nreason"),
        ("provider_name", ""),
        ("attempt_number", 0),
        ("fallback_count", -1),
        ("cache_hit", 1),
        ("cache_age_seconds", -0.1),
        ("checked_at", datetime(2026, 1, 1)),
    ],
)
def test_semantic_result_rejects_invalid_metadata(
    field_name: str, value: object
) -> None:
    with pytest.raises(ValueError):
        replace(_semantic(), **{field_name: value})


@pytest.mark.parametrize(
    "changes",
    [
        {"is_duplicate": True, "matched_post_id": None},
        {"is_duplicate": False, "matched_post_id": PostId("candidate")},
    ],
)
def test_exact_duplicate_result_requires_consistent_identity(
    changes: dict[str, object],
) -> None:
    result = DuplicateCheckResult(
        False,
        None,
        "exact",
        1,
        1,
        "hash",
        NOW,
    )
    with pytest.raises(ValueError):
        replace(result, **changes)
    with pytest.raises(ValueError):
        replace(result, checked_at=datetime(2026, 1, 1))


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("policy", "retry_later"),
        ("failure_category", " "),
        ("failed_at", datetime(2026, 1, 1)),
    ],
)
def test_semantic_failure_rejects_invalid_metadata(
    field_name: str, value: object
) -> None:
    failure = SemanticDuplicateFailure(
        SemanticDuplicateFailurePolicy.RETRY_LATER,
        "timeout",
        NOW,
        NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError):
        replace(failure, **{field_name: value})
    with pytest.raises(ValueError):
        replace(failure, next_retry_at=NOW)


def _score() -> ScoringResult:
    return ScoringResult(
        80,
        0.8,
        "useful",
        "provider",
        "model",
        NOW,
        "1",
        "1",
    )


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("score", True),
        ("score", 101),
        ("confidence", 1),
        ("confidence", 1.1),
        ("reason", " "),
        ("provider_name", ""),
        ("attractiveness_probability", 1),
        ("attractiveness_probability", 1.1),
        ("headline_quality", True),
        ("headline_quality", 101),
        ("cache_hit", 1),
        ("cache_age_seconds", -0.1),
        ("attempt_number", -1),
        ("scored_at", datetime(2026, 1, 1)),
    ],
)
def test_scoring_result_rejects_invalid_metadata(
    field_name: str, value: object
) -> None:
    with pytest.raises(ValueError):
        replace(_score(), **{field_name: value})


def test_scoring_failure_requires_future_retry_and_typed_policy() -> None:
    failure = ScoringFailure(
        ScoringFailurePolicy.RETRY_LATER,
        "timeout",
        NOW,
        NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError):
        replace(failure, policy="retry_later")
    with pytest.raises(ValueError):
        replace(failure, next_retry_at=NOW)


def _reservation() -> ActiveReservation:
    return ActiveReservation(
        "reservation",
        "owner",
        ReservationKind.NORMAL,
        NOW,
        NOW + timedelta(seconds=30),
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"reservation_id": " "},
        {"owner_id": ""},
        {"created_at": datetime(2026, 1, 1)},
        {"expires_at": NOW},
    ],
)
def test_provider_reservation_rejects_invalid_lease(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(_reservation(), **changes)


@pytest.mark.parametrize(
    "changes",
    [
        {"success": True, "health_failure": True},
        {"cancelled": True, "health_failure": True},
        {
            "rate_limited": True,
            "failure_category": ProviderFailureCategory.TRANSIENT,
        },
        {"retry_after_seconds": 1.0},
        {
            "rate_limited": True,
            "failure_category": ProviderFailureCategory.RATE_LIMIT,
            "retry_after_seconds": -1.0,
        },
        {"cooldown_until": NOW},
    ],
)
def test_provider_attempt_outcome_rejects_contradictions(
    changes: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        replace(ProviderAttemptOutcome(False), **changes)


def test_provider_health_validates_identity_counters_and_naive_timestamps() -> None:
    with pytest.raises(ValueError):
        ProviderHealth(" ", "model")
    with pytest.raises(ValueError):
        ProviderHealth("provider", "model", failure_count=-1)
    with pytest.raises(ValueError):
        ProviderHealth(
            "provider",
            "model",
            request_window_start=datetime(2026, 1, 1),
        )


def test_rate_limit_cooldown_precedence_and_fallback_are_explicit() -> None:
    state = ProviderHealth(
        "provider",
        "model",
        active_reservations=(_reservation(),),
    )
    explicit = state.record_outcome(
        "reservation",
        "owner",
        ProviderAttemptOutcome(
            False,
            ProviderFailureCategory.RATE_LIMIT,
            rate_limited=True,
            cooldown_until=NOW + timedelta(seconds=5),
        ),
        NOW,
        failure_threshold=3,
        open_seconds=10,
        fallback_cooldown_seconds=30,
    )
    assert explicit.cooldown_until == NOW + timedelta(seconds=5)
    fallback = state.record_outcome(
        "reservation",
        "owner",
        ProviderAttemptOutcome(
            False,
            ProviderFailureCategory.RATE_LIMIT,
            rate_limited=True,
        ),
        NOW,
        failure_threshold=3,
        open_seconds=10,
        fallback_cooldown_seconds=30,
    )
    assert fallback.cooldown_until == NOW + timedelta(seconds=30)
