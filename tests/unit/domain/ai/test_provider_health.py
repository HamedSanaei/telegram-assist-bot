"""Unit tests for Provider/Model capacity and circuit state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from telegram_assist_bot.domain.ai.provider_health import (
    ActiveReservation,
    CircuitState,
    IneligibilityReason,
    ProviderAttemptOutcome,
    ProviderFailureCategory,
    ProviderHealth,
    ReservationKind,
)

NOW = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)


def reservation(
    identifier: str,
    owner: str,
    *,
    kind: ReservationKind = ReservationKind.NORMAL,
    expires_at: datetime | None = None,
) -> ActiveReservation:
    """Build one deterministic reservation."""
    return ActiveReservation(
        reservation_id=identifier,
        owner_id=owner,
        kind=kind,
        created_at=NOW - timedelta(seconds=1),
        expires_at=expires_at or NOW + timedelta(seconds=30),
    )


def policy() -> dict[str, int]:
    """Return common pure eligibility limits."""
    return {
        "concurrency_limit": 2,
        "request_limit": 3,
        "request_window_seconds": 60,
    }


def test_concurrency_greater_than_one_and_expiry_reclamation() -> None:
    state = ProviderHealth(
        "provider",
        "model",
        active_reservations=(
            reservation("expired", "old", expires_at=NOW),
            reservation("one", "worker-one"),
        ),
    )

    assert state.eligibility(NOW, **policy()).allowed is True
    full = ProviderHealth(
        "provider",
        "model",
        active_reservations=(
            reservation("one", "worker-one"),
            reservation("two", "worker-two"),
        ),
    )
    decision = full.eligibility(NOW, **policy())
    assert decision.reason is IneligibilityReason.CONCURRENCY_LIMIT
    assert decision.next_eligible_at == NOW + timedelta(seconds=30)


def test_fixed_request_window_has_exact_boundary() -> None:
    state = ProviderHealth(
        "provider",
        "model",
        request_window_start=NOW,
        request_count=3,
    )

    blocked = state.eligibility(NOW + timedelta(seconds=59), **policy())
    exact = state.eligibility(NOW + timedelta(seconds=60), **policy())
    assert blocked.reason is IneligibilityReason.REQUEST_WINDOW_LIMIT
    assert exact.allowed is True


def test_closed_opens_at_exact_threshold_and_success_resets() -> None:
    state = ProviderHealth(
        "provider",
        "model",
        failure_count=1,
        active_reservations=(reservation("r", "owner"),),
    )
    failure = ProviderAttemptOutcome(
        success=False,
        failure_category=ProviderFailureCategory.TRANSIENT,
        health_failure=True,
    )

    opened = state.record_outcome(
        "r",
        "owner",
        failure,
        NOW,
        failure_threshold=2,
        open_seconds=10,
        fallback_cooldown_seconds=None,
    )
    assert opened.circuit_state is CircuitState.OPEN
    assert opened.failure_count == 2
    assert opened.open_until == NOW + timedelta(seconds=10)

    closed = ProviderHealth(
        "provider",
        "model",
        failure_count=1,
        active_reservations=(reservation("s", "owner"),),
    ).record_outcome(
        "s",
        "owner",
        ProviderAttemptOutcome.succeeded(),
        NOW,
        failure_threshold=2,
        open_seconds=10,
        fallback_cooldown_seconds=None,
    )
    assert closed.failure_count == 0
    assert closed.circuit_state is CircuitState.CLOSED


def test_open_expiry_allows_one_probe_and_probe_result_transitions() -> None:
    open_state = ProviderHealth(
        "provider",
        "model",
        circuit_state=CircuitState.OPEN,
        failure_count=2,
        open_until=NOW,
    )
    eligible = open_state.eligibility(NOW, **policy())
    assert eligible.allowed is True
    assert eligible.reservation_kind is ReservationKind.HALF_OPEN_PROBE

    probe = reservation("probe", "owner", kind=ReservationKind.HALF_OPEN_PROBE)
    half_open = open_state.with_reservation(
        probe,
        NOW,
        request_window_seconds=60,
    )
    blocked = half_open.eligibility(NOW, **policy())
    assert blocked.reason is IneligibilityReason.HALF_OPEN_PROBE_ACTIVE

    succeeded = half_open.record_outcome(
        "probe",
        "owner",
        ProviderAttemptOutcome.succeeded(),
        NOW,
        failure_threshold=2,
        open_seconds=10,
        fallback_cooldown_seconds=None,
    )
    assert succeeded.circuit_state is CircuitState.CLOSED
    assert succeeded.failure_count == 0

    failed = half_open.record_outcome(
        "probe",
        "owner",
        ProviderAttemptOutcome(
            success=False,
            failure_category=ProviderFailureCategory.TIMEOUT,
            health_failure=True,
        ),
        NOW,
        failure_threshold=2,
        open_seconds=10,
        fallback_cooldown_seconds=None,
    )
    assert failed.circuit_state is CircuitState.OPEN
    assert failed.open_until == NOW + timedelta(seconds=10)


def test_only_typed_rate_limit_applies_cooldown() -> None:
    base = ProviderHealth(
        "provider",
        "model",
        active_reservations=(reservation("r", "owner"),),
    )
    ordinary = base.record_outcome(
        "r",
        "owner",
        ProviderAttemptOutcome(
            success=False,
            failure_category=ProviderFailureCategory.TRANSIENT,
            health_failure=True,
        ),
        NOW,
        failure_threshold=5,
        open_seconds=10,
        fallback_cooldown_seconds=30,
    )
    assert ordinary.cooldown_until is None

    rate_limited = base.record_outcome(
        "r",
        "owner",
        ProviderAttemptOutcome(
            success=False,
            failure_category=ProviderFailureCategory.RATE_LIMIT,
            health_failure=True,
            rate_limited=True,
            retry_after_seconds=12,
        ),
        NOW,
        failure_threshold=5,
        open_seconds=10,
        fallback_cooldown_seconds=30,
    )
    assert rate_limited.cooldown_until == NOW + timedelta(seconds=12)


def test_non_health_outcomes_and_wrong_owner_are_idempotent() -> None:
    state = ProviderHealth(
        "provider",
        "model",
        failure_count=1,
        active_reservations=(reservation("r", "owner"),),
    )
    unchanged = state.record_outcome(
        "r",
        "different-owner",
        ProviderAttemptOutcome.succeeded(),
        NOW,
        failure_threshold=2,
        open_seconds=10,
        fallback_cooldown_seconds=None,
    )
    assert unchanged is state

    auth = state.record_outcome(
        "r",
        "owner",
        ProviderAttemptOutcome(
            success=False,
            failure_category=ProviderFailureCategory.AUTHORIZATION,
        ),
        NOW,
        failure_threshold=2,
        open_seconds=10,
        fallback_cooldown_seconds=None,
    )
    assert auth.failure_count == 1
    assert auth.active_reservations == ()
