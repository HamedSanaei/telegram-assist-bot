"""Pure Provider/Model capacity and circuit-breaker domain state."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class CircuitState(StrEnum):
    """Persistent circuit-breaker states."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class ReservationKind(StrEnum):
    """Kinds of capacity reservation."""

    NORMAL = "normal"
    HALF_OPEN_PROBE = "half_open_probe"


class IneligibilityReason(StrEnum):
    """Safe reasons why an external attempt cannot start now."""

    CIRCUIT_OPEN = "circuit_open"
    HALF_OPEN_PROBE_ACTIVE = "half_open_probe_active"
    COOLDOWN_ACTIVE = "cooldown_active"
    CONCURRENCY_LIMIT = "concurrency_limit"
    REQUEST_WINDOW_LIMIT = "request_window_limit"


class ProviderFailureCategory(StrEnum):
    """Sanitized provider-health outcome categories owned by the domain."""

    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    AUTHORIZATION = "authorization"
    PERMISSION = "permission"
    PERMANENT = "permanent"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    CONCURRENCY_CONFLICT = "concurrency_conflict"
    ALREADY_COMPLETED = "already_completed"


def _require_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class ActiveReservation:
    """One independently owned, expiring provider capacity reservation."""

    reservation_id: str
    owner_id: str
    kind: ReservationKind
    created_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        """Validate identity, ownership and UTC lifetime."""
        if not self.reservation_id.strip() or not self.owner_id.strip():
            raise ValueError("reservation and owner identifiers must not be blank")
        created = _require_utc(self.created_at, "created_at")
        expires = _require_utc(self.expires_at, "expires_at")
        if expires <= created:
            raise ValueError("reservation expiry must be after creation")
        object.__setattr__(self, "created_at", created)
        object.__setattr__(self, "expires_at", expires)


@dataclass(frozen=True, slots=True)
class ProviderAttemptOutcome:
    """Typed, sanitized outcome recorded after one external provider attempt."""

    success: bool
    failure_category: ProviderFailureCategory | None = None
    health_failure: bool = False
    rate_limited: bool = False
    retry_after_seconds: float | None = None
    cooldown_until: datetime | None = None
    cancelled: bool = False

    def __post_init__(self) -> None:
        """Reject contradictory outcome metadata."""
        if self.success and (
            self.failure_category is not None
            or self.health_failure
            or self.rate_limited
            or self.cancelled
        ):
            raise ValueError("successful outcome cannot contain failure metadata")
        if self.cancelled and (self.health_failure or self.rate_limited):
            raise ValueError("cancellation cannot affect provider health")
        if (
            self.rate_limited
            and self.failure_category is not ProviderFailureCategory.RATE_LIMIT
        ):
            raise ValueError("rate-limited outcome requires rate_limit category")
        if self.retry_after_seconds is not None and (
            self.retry_after_seconds < 0 or not self.rate_limited
        ):
            raise ValueError("retry_after_seconds requires a rate-limit outcome")
        if self.cooldown_until is not None:
            if not self.rate_limited:
                raise ValueError("cooldown_until requires a rate-limit outcome")
            object.__setattr__(
                self,
                "cooldown_until",
                _require_utc(self.cooldown_until, "cooldown_until"),
            )

    @classmethod
    def succeeded(cls) -> ProviderAttemptOutcome:
        """Build a successful outcome."""
        return cls(success=True)

    @classmethod
    def cancelled_by_caller(cls) -> ProviderAttemptOutcome:
        """Build a caller-cancellation outcome."""
        return cls(
            success=False,
            failure_category=ProviderFailureCategory.PERMANENT,
            cancelled=True,
        )


@dataclass(frozen=True, slots=True)
class Eligibility:
    """Pure eligibility decision and nearest known retry time."""

    allowed: bool
    reason: IneligibilityReason | None = None
    next_eligible_at: datetime | None = None
    reservation_kind: ReservationKind = ReservationKind.NORMAL


@dataclass(frozen=True, slots=True)
class ProviderHealth:
    """Persistent health state for one logical Provider/Model pair."""

    provider_name: str
    model_name: str
    circuit_state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    open_until: datetime | None = None
    cooldown_until: datetime | None = None
    request_window_start: datetime | None = None
    request_count: int = 0
    active_reservations: tuple[ActiveReservation, ...] = field(default_factory=tuple)
    version: int = 0

    def __post_init__(self) -> None:
        """Validate persistent invariants and normalize timestamps to UTC."""
        if not self.provider_name.strip() or not self.model_name.strip():
            raise ValueError("provider and model names must not be blank")
        if self.failure_count < 0 or self.request_count < 0 or self.version < 0:
            raise ValueError("provider counters must not be negative")
        for field_name in ("open_until", "cooldown_until", "request_window_start"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _require_utc(value, field_name))

    def live_reservations(self, now: datetime) -> tuple[ActiveReservation, ...]:
        """Return reservations whose leases have not expired at ``now``."""
        current = _require_utc(now, "now")
        return tuple(
            item for item in self.active_reservations if item.expires_at > current
        )

    def eligibility(
        self,
        now: datetime,
        *,
        concurrency_limit: int,
        request_limit: int,
        request_window_seconds: int,
    ) -> Eligibility:
        """Evaluate fixed-window, cooldown, concurrency and circuit constraints."""
        current = _require_utc(now, "now")
        live = self.live_reservations(current)
        probe_active = any(
            item.kind is ReservationKind.HALF_OPEN_PROBE for item in live
        )
        kind = ReservationKind.NORMAL
        if self.circuit_state is CircuitState.OPEN:
            if self.open_until is None or current < self.open_until:
                return Eligibility(
                    False,
                    IneligibilityReason.CIRCUIT_OPEN,
                    self.open_until,
                )
            kind = ReservationKind.HALF_OPEN_PROBE
        elif self.circuit_state is CircuitState.HALF_OPEN:
            kind = ReservationKind.HALF_OPEN_PROBE
        if kind is ReservationKind.HALF_OPEN_PROBE and probe_active:
            probe_expiry = min(
                item.expires_at
                for item in live
                if item.kind is ReservationKind.HALF_OPEN_PROBE
            )
            return Eligibility(
                False,
                IneligibilityReason.HALF_OPEN_PROBE_ACTIVE,
                probe_expiry,
                kind,
            )
        if self.cooldown_until is not None and current < self.cooldown_until:
            return Eligibility(
                False,
                IneligibilityReason.COOLDOWN_ACTIVE,
                self.cooldown_until,
                kind,
            )
        if len(live) >= concurrency_limit:
            return Eligibility(
                False,
                IneligibilityReason.CONCURRENCY_LIMIT,
                min(item.expires_at for item in live),
                kind,
            )
        if self.request_window_start is not None:
            window_end = self.request_window_start + timedelta(
                seconds=request_window_seconds
            )
            if current < window_end and self.request_count >= request_limit:
                return Eligibility(
                    False,
                    IneligibilityReason.REQUEST_WINDOW_LIMIT,
                    window_end,
                    kind,
                )
        return Eligibility(True, reservation_kind=kind)

    def with_reservation(
        self,
        reservation: ActiveReservation,
        now: datetime,
        *,
        request_window_seconds: int,
    ) -> ProviderHealth:
        """Return the state produced by a successful atomic reservation."""
        current = _require_utc(now, "now")
        window_start = self.request_window_start
        request_count = self.request_count
        if window_start is None or current >= window_start + timedelta(
            seconds=request_window_seconds
        ):
            window_start = current
            request_count = 0
        circuit = self.circuit_state
        if reservation.kind is ReservationKind.HALF_OPEN_PROBE:
            circuit = CircuitState.HALF_OPEN
        return replace(
            self,
            circuit_state=circuit,
            request_window_start=window_start,
            request_count=request_count + 1,
            active_reservations=(*self.live_reservations(current), reservation),
            version=self.version + 1,
        )

    def record_outcome(
        self,
        reservation_id: str,
        owner_id: str,
        outcome: ProviderAttemptOutcome,
        now: datetime,
        *,
        failure_threshold: int,
        open_seconds: int,
        fallback_cooldown_seconds: int | None,
    ) -> ProviderHealth:
        """Release an owned reservation and apply its typed health outcome."""
        current = _require_utc(now, "now")
        match = next(
            (
                item
                for item in self.active_reservations
                if item.reservation_id == reservation_id and item.owner_id == owner_id
            ),
            None,
        )
        if match is None:
            return self
        remaining = tuple(
            item
            for item in self.active_reservations
            if item.reservation_id != reservation_id and item.expires_at > current
        )
        circuit = self.circuit_state
        failures = self.failure_count
        open_until = self.open_until
        cooldown_until = self.cooldown_until
        if outcome.success:
            failures = 0
            if match.kind is ReservationKind.HALF_OPEN_PROBE:
                circuit = CircuitState.CLOSED
                open_until = None
        elif outcome.health_failure and not outcome.cancelled:
            failures += 1
            should_open = (
                match.kind is ReservationKind.HALF_OPEN_PROBE
                or failures >= failure_threshold
            )
            if should_open:
                circuit = CircuitState.OPEN
                open_until = current + timedelta(seconds=open_seconds)
        if outcome.rate_limited:
            if outcome.cooldown_until is not None:
                cooldown_until = outcome.cooldown_until
            elif outcome.retry_after_seconds is not None:
                cooldown_until = current + timedelta(
                    seconds=outcome.retry_after_seconds
                )
            elif fallback_cooldown_seconds is not None:
                cooldown_until = current + timedelta(seconds=fallback_cooldown_seconds)
        return replace(
            self,
            circuit_state=circuit,
            failure_count=failures,
            open_until=open_until,
            cooldown_until=cooldown_until,
            active_reservations=remaining,
            version=self.version + 1,
        )


__all__ = (
    "ActiveReservation",
    "CircuitState",
    "Eligibility",
    "IneligibilityReason",
    "ProviderAttemptOutcome",
    "ProviderFailureCategory",
    "ProviderHealth",
    "ReservationKind",
)
