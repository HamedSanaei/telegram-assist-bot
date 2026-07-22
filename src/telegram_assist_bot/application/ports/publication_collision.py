"""Application-owned persistence boundary for T052 collision resolution."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.publication_collision import (
        CollisionAdvertisement,
        CollisionNormalPublication,
        PublicationCollisionPlan,
    )


@dataclass(frozen=True, slots=True)
class PublicationCollisionSnapshot:
    """Consistent minimum projections for one destination."""

    advertisements: tuple[CollisionAdvertisement, ...]
    normal_publications: tuple[CollisionNormalPublication, ...]


class CollisionApplyOutcome(StrEnum):
    """Typed CAS application outcome."""

    APPLIED = "applied"
    IDEMPOTENT = "idempotent"
    CONFLICT = "conflict"


class PublicationCollisionRepository(Protocol):
    """Load and conditionally apply one destination collision plan."""

    async def load_destination(
        self, destination_id: int
    ) -> PublicationCollisionSnapshot:
        """Load only scheduling metadata, never publication content."""
        ...

    async def apply_plan(
        self,
        destination_id: int,
        plan: PublicationCollisionPlan,
        *,
        occurred_at: datetime,
    ) -> CollisionApplyOutcome:
        """Apply each move with version/status CAS and idempotent recovery."""
        ...


__all__ = (
    "CollisionApplyOutcome",
    "PublicationCollisionRepository",
    "PublicationCollisionSnapshot",
)
