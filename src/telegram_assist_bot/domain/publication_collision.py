"""Pure deterministic advertisement/normal publication collision planning."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class CollisionResolutionState(StrEnum):
    """Persistent readiness of one advertisement slot for publication."""

    UNRESOLVED = "unresolved"
    RESOLVED = "resolved"


class CollisionResolutionOutcome(StrEnum):
    """Typed result of one destination collision resolution attempt."""

    NO_SLOTS = "no_slots"
    RESOLVED = "resolved"
    ALREADY_RESOLVED = "already_resolved"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class CollisionAdvertisement:
    """Minimum advertisement projection required by the pure planner."""

    slot_id: str
    campaign_id: str
    original_due_at: datetime
    current_due_at: datetime
    minimum_gap_seconds: int
    priority: int
    version: int
    movable: bool
    resolved: bool


@dataclass(frozen=True, slots=True)
class CollisionNormalPublication:
    """Minimum normal scheduled-publication projection required by the planner."""

    job_id: str
    due_at: datetime
    version: int
    movable: bool


@dataclass(frozen=True, slots=True)
class AdvertisementCollisionMove:
    """One CAS-protected advertisement effective-time decision."""

    slot_id: str
    expected_version: int
    old_due_at: datetime
    new_due_at: datetime


@dataclass(frozen=True, slots=True)
class NormalCollisionMove:
    """One CAS-protected normal queue due-time change."""

    job_id: str
    expected_version: int
    old_due_at: datetime
    new_due_at: datetime


@dataclass(frozen=True, slots=True)
class PublicationCollisionPlan:
    """Deterministic set of advertisement placements and normal queue moves."""

    advertisement_moves: tuple[AdvertisementCollisionMove, ...]
    normal_moves: tuple[NormalCollisionMove, ...]
    immutable_conflict_ids: tuple[str, ...] = ()


def plan_publication_collisions(
    advertisements: tuple[CollisionAdvertisement, ...],
    normal_publications: tuple[CollisionNormalPublication, ...],
) -> PublicationCollisionPlan:
    """Place advertisements by approved priority then shift movable normal jobs."""
    fixed = [item for item in advertisements if not item.movable]
    movable = [item for item in advertisements if item.movable]
    placed: list[tuple[datetime, int, str]] = [
        (item.current_due_at.astimezone(UTC), item.minimum_gap_seconds, item.slot_id)
        for item in fixed
    ]
    advertisement_moves: list[AdvertisementCollisionMove] = []
    for item in sorted(
        movable,
        key=lambda value: (
            -value.priority,
            value.campaign_id,
            value.original_due_at,
            value.slot_id,
        ),
    ):
        candidate = item.original_due_at.astimezone(UTC)
        while True:
            advertisement_collision = next(
                (
                    existing
                    for existing in sorted(placed)
                    if abs((candidate - existing[0]).total_seconds())
                    < max(item.minimum_gap_seconds, existing[1])
                ),
                None,
            )
            if advertisement_collision is None:
                break
            candidate = advertisement_collision[0] + timedelta(
                seconds=max(item.minimum_gap_seconds, advertisement_collision[1])
            )
        placed.append((candidate, item.minimum_gap_seconds, item.slot_id))
        advertisement_moves.append(
            AdvertisementCollisionMove(
                item.slot_id,
                item.version,
                item.current_due_at.astimezone(UTC),
                candidate,
            )
        )

    all_ads = sorted(placed)
    immutable_conflicts: set[str] = set()
    normal_moves: list[NormalCollisionMove] = []
    previous_original: datetime | None = None
    previous_effective: datetime | None = None
    for normal in sorted(
        normal_publications, key=lambda item: (item.due_at, item.job_id)
    ):
        original = normal.due_at.astimezone(UTC)
        if not normal.movable:
            for ad_due, gap, slot_id in all_ads:
                if abs((original - ad_due).total_seconds()) < gap:
                    immutable_conflicts.add(f"{slot_id}:{normal.job_id}")
            continue
        candidate = original
        if previous_original is not None and previous_effective is not None:
            candidate = max(
                candidate, previous_effective + (original - previous_original)
            )
        while True:
            normal_collision = next(
                (
                    (ad_due, gap)
                    for ad_due, gap, _slot_id in all_ads
                    if abs((candidate - ad_due).total_seconds()) < gap
                ),
                None,
            )
            if normal_collision is None:
                break
            candidate = normal_collision[0] + timedelta(seconds=normal_collision[1])
        if candidate != original:
            normal_moves.append(
                NormalCollisionMove(normal.job_id, normal.version, original, candidate)
            )
        previous_original = original
        previous_effective = candidate

    return PublicationCollisionPlan(
        tuple(sorted(advertisement_moves, key=lambda item: item.slot_id)),
        tuple(normal_moves),
        tuple(sorted(immutable_conflicts)),
    )


__all__ = (
    "AdvertisementCollisionMove",
    "CollisionAdvertisement",
    "CollisionNormalPublication",
    "CollisionResolutionOutcome",
    "CollisionResolutionState",
    "NormalCollisionMove",
    "PublicationCollisionPlan",
    "plan_publication_collisions",
)
