"""Resolve advertisement priority and minimum-gap collisions without Telegram."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports.publication_collision import (
    CollisionApplyOutcome,
)
from telegram_assist_bot.domain.publication_collision import (
    CollisionResolutionOutcome,
    plan_publication_collisions,
)

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.application.ports.publication_collision import (
        PublicationCollisionRepository,
    )


@dataclass(frozen=True, slots=True)
class ResolvePublicationCollisionResult:
    """Summarize one destination resolution without exposing payloads."""

    outcome: CollisionResolutionOutcome
    advertisement_move_count: int = 0
    normal_move_count: int = 0
    immutable_conflict_count: int = 0


class ResolvePublicationCollision:
    """Build and persist the approved deterministic T052 plan."""

    def __init__(
        self,
        repository: PublicationCollisionRepository,
        clock: Clock,
        *,
        max_cas_attempts: int,
    ) -> None:
        """Store the persistence boundary and deterministic clock."""
        if type(max_cas_attempts) is not int or not 1 <= max_cas_attempts <= 10:
            raise ValueError("collision CAS attempts must be between 1 and 10")
        self._repository = repository
        self._clock = clock
        self._max_cas_attempts = max_cas_attempts

    async def execute(self, destination_id: int) -> ResolvePublicationCollisionResult:
        """Resolve one destination once; callers may retry a typed CAS conflict."""
        for attempt in range(self._max_cas_attempts):
            snapshot = await self._repository.load_destination(destination_id)
            if not snapshot.advertisements:
                return ResolvePublicationCollisionResult(
                    CollisionResolutionOutcome.NO_SLOTS
                )
            plan = plan_publication_collisions(
                snapshot.advertisements, snapshot.normal_publications
            )
            if (
                all(item.resolved for item in snapshot.advertisements)
                and not plan.normal_moves
            ):
                return ResolvePublicationCollisionResult(
                    CollisionResolutionOutcome.ALREADY_RESOLVED
                )
            applied = await self._repository.apply_plan(
                destination_id, plan, occurred_at=self._aware_now()
            )
            if applied is not CollisionApplyOutcome.CONFLICT:
                return ResolvePublicationCollisionResult(
                    CollisionResolutionOutcome.RESOLVED,
                    len(plan.advertisement_moves),
                    len(plan.normal_moves),
                    len(plan.immutable_conflict_ids),
                )
            if attempt + 1 == self._max_cas_attempts:
                break
        return ResolvePublicationCollisionResult(CollisionResolutionOutcome.CONFLICT)

    def _aware_now(self) -> datetime:
        value = self._clock.utc_now()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("collision clock must return an aware instant")
        return value


__all__ = (
    "ResolvePublicationCollision",
    "ResolvePublicationCollisionResult",
)
