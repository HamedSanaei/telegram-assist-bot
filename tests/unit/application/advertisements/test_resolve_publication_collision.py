"""Unit tests for the approved deterministic T052 collision policy."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from telegram_assist_bot.application.advertisements.resolve_publication_collision import (  # noqa: E501
    ResolvePublicationCollision,
)
from telegram_assist_bot.application.ports.publication_collision import (
    CollisionApplyOutcome,
    PublicationCollisionSnapshot,
)
from telegram_assist_bot.domain.publication_collision import (
    CollisionAdvertisement,
    CollisionNormalPublication,
    CollisionResolutionOutcome,
    PublicationCollisionPlan,
    plan_publication_collisions,
)

NOW = datetime(2026, 7, 22, 10, tzinfo=UTC)


class FixedClock:
    def utc_now(self) -> datetime:
        return NOW


class Repository:
    def __init__(
        self,
        snapshot: PublicationCollisionSnapshot,
        outcome: CollisionApplyOutcome = CollisionApplyOutcome.APPLIED,
    ) -> None:
        self.snapshot = snapshot
        self.outcome = outcome
        self.plan: PublicationCollisionPlan | None = None

    async def load_destination(
        self, destination_id: int
    ) -> PublicationCollisionSnapshot:
        assert destination_id == -1001
        return self.snapshot

    async def apply_plan(
        self,
        destination_id: int,
        plan: PublicationCollisionPlan,
        *,
        occurred_at: datetime,
    ) -> CollisionApplyOutcome:
        assert destination_id == -1001
        assert occurred_at == NOW
        self.plan = plan
        return self.outcome


def advertisement(
    slot_id: str,
    minute: int,
    *,
    priority: int = 1,
    campaign_id: str | None = None,
    gap: int = 300,
    movable: bool = True,
    resolved: bool = False,
) -> CollisionAdvertisement:
    due = NOW + timedelta(minutes=minute)
    return CollisionAdvertisement(
        slot_id,
        campaign_id or slot_id,
        due,
        due,
        gap,
        priority,
        0,
        movable,
        resolved,
    )


def normal(
    job_id: str, minute: int, *, movable: bool = True
) -> CollisionNormalPublication:
    return CollisionNormalPublication(
        job_id, NOW + timedelta(minutes=minute), 0, movable
    )


def test_exact_minimum_gap_boundary_is_not_a_collision() -> None:
    plan = plan_publication_collisions(
        (advertisement("ad", 10),), (normal("normal", 5), normal("later", 15))
    )
    assert plan.normal_moves == ()
    assert plan.advertisement_moves[0].new_due_at == NOW + timedelta(minutes=10)


def test_normal_jobs_inside_gap_move_after_ad_and_preserve_queue_order() -> None:
    plan = plan_publication_collisions(
        (advertisement("ad", 10),),
        (normal("one", 8), normal("two", 9), normal("three", 16)),
    )
    assert [(item.job_id, item.new_due_at) for item in plan.normal_moves] == [
        ("one", NOW + timedelta(minutes=15)),
        ("two", NOW + timedelta(minutes=16)),
        ("three", NOW + timedelta(minutes=23)),
    ]


def test_advertisement_priority_then_lexical_campaign_order_is_deterministic() -> None:
    plan = plan_publication_collisions(
        (
            advertisement("low", 10, priority=1, campaign_id="zeta"),
            advertisement("lexical-second", 10, priority=3, campaign_id="beta"),
            advertisement("winner", 10, priority=3, campaign_id="alpha"),
        ),
        (),
    )
    effective = {item.slot_id: item.new_due_at for item in plan.advertisement_moves}
    assert effective == {
        "winner": NOW + timedelta(minutes=10),
        "lexical-second": NOW + timedelta(minutes=15),
        "low": NOW + timedelta(minutes=20),
    }


def test_executing_jobs_are_never_moved_and_conflict_is_explicit() -> None:
    plan = plan_publication_collisions(
        (advertisement("ad", 10),), (normal("running", 9, movable=False),)
    )
    assert plan.normal_moves == ()
    assert plan.immutable_conflict_ids == ("ad:running",)


def test_destination_resolver_applies_plan_and_maps_cas_conflict() -> None:
    async def scenario() -> None:
        snapshot = PublicationCollisionSnapshot(
            (advertisement("ad", 10),), (normal("normal", 9),)
        )
        repository = Repository(snapshot)
        result = await ResolvePublicationCollision(
            repository, FixedClock(), max_cas_attempts=1
        ).execute(-1001)
        assert result.outcome is CollisionResolutionOutcome.RESOLVED
        assert result.normal_move_count == 1
        assert repository.plan is not None

        repository.outcome = CollisionApplyOutcome.CONFLICT
        conflict = await ResolvePublicationCollision(
            repository, FixedClock(), max_cas_attempts=1
        ).execute(-1001)
        assert conflict.outcome is CollisionResolutionOutcome.CONFLICT

    asyncio.run(scenario())


def test_empty_and_already_resolved_destinations_are_idempotent() -> None:
    async def scenario() -> None:
        empty = Repository(PublicationCollisionSnapshot((), ()))
        result = await ResolvePublicationCollision(
            empty, FixedClock(), max_cas_attempts=1
        ).execute(-1001)
        assert result.outcome is CollisionResolutionOutcome.NO_SLOTS

        resolved = Repository(
            PublicationCollisionSnapshot(
                (advertisement("ad", 10, movable=False, resolved=True),), ()
            )
        )
        again = await ResolvePublicationCollision(
            resolved, FixedClock(), max_cas_attempts=1
        ).execute(-1001)
        assert again.outcome is CollisionResolutionOutcome.ALREADY_RESOLVED

    asyncio.run(scenario())
