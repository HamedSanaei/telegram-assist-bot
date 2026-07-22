"""Use Case for enqueuing AI Jobs."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from telegram_assist_bot.domain.ai_job import AIJob

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports.ai_job_repository import (
        AIJobRepository,
        EnqueueJobResult,
    )
    from telegram_assist_bot.application.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class EnqueueAIJob:
    """Use case to idempotently enqueue an AI Job."""

    repository: AIJobRepository = field(repr=False)
    clock: Clock = field(repr=False)

    async def execute(
        self,
        post_id: str,
        task_type: str,
        prompt_version: str,
        schema_version: str,
        priority: int,
        max_attempts: int = 3,
        job_id: str | None = None,
        next_run_at: datetime | None = None,
    ) -> EnqueueJobResult:
        """Enqueue a new AI Job, returning the result containing outcome and job."""
        now = self.clock.utc_now()
        effective_job_id = job_id or f"job_{uuid.uuid4().hex}"

        job = AIJob.create(
            job_id=effective_job_id,
            post_id=post_id,
            task_type=task_type,
            prompt_version=prompt_version,
            schema_version=schema_version,
            priority=priority,
            max_attempts=max_attempts,
            created_at=now,
            next_run_at=next_run_at,
        )

        return await self.repository.enqueue(job)
