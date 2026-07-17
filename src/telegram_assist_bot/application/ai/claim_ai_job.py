"""Use Case for claiming AI Jobs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from telegram_assist_bot.application.ports.ai_job_repository import AIJobRepository
    from telegram_assist_bot.application.ports.clock import Clock
    from telegram_assist_bot.domain.ai_job import AIJob


@dataclass(frozen=True, slots=True)
class ClaimAIJob:
    """Use case to claim the next eligible AI Job from the queue."""

    repository: AIJobRepository = field(repr=False)
    clock: Clock = field(repr=False)

    async def execute(
        self,
        owner: str,
        lease_duration_seconds: float,
    ) -> AIJob | None:
        """Claim the next eligible AI Job using an atomic MongoDB update."""
        now = self.clock.utc_now()
        return await self.repository.claim_next_due(
            owner=owner,
            lease_duration_seconds=lease_duration_seconds,
            as_of=now,
        )
