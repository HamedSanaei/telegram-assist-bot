"""Application-owned persistence port for AI Jobs."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.ai_job import AIJob


class EnqueueJobOutcome(StrEnum):
    """The result outcome of an enqueue operation."""

    CREATED = "Created"
    ALREADY_EXISTS = "AlreadyExists"


@dataclass(frozen=True)
class EnqueueJobResult:
    """The result of an enqueue attempt containing the outcome and job."""

    outcome: EnqueueJobOutcome
    job: AIJob


class AIJobRepositoryError(Exception):
    """Base exception for all AI Job Repository operations."""


class AIJobNotFoundError(AIJobRepositoryError):
    """Raised when an AI Job is not found."""


class AIJobConcurrencyConflictError(AIJobRepositoryError):
    """Raised when a concurrency conflict is detected (optimistic locking)."""


class AIJobRepository(Protocol):
    """Protocol defining persistence operations for AI Jobs."""

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        """Idempotently insert an AI Job.

        If a job with the same idempotency_key already exists, returns
        ALREADY_EXISTS along with the existing job document, without modifying it.
        """
        ...

    async def claim_next_due(
        self,
        owner: str,
        lease_duration_seconds: float,
        as_of: datetime,
    ) -> AIJob | None:
        """Atomically claim the next eligible due job.

        Finds a job in Pending or WaitingForRetry state (where next_run_at <= as_of),
        or a job in Processing whose lease has expired (lease_expires_at < as_of).

        Ordering:
          1. Priority (descending)
          2. next_run_at (ascending)
          3. created_at (ascending)

        Atomically claims it by changing status to Processing, updating owner,
        incrementing attempts, and setting the lease expiry.
        """
        ...

    async def update(self, job: AIJob) -> None:
        """Update an AI Job using optimistic concurrency control.

        Verifies matching job_id and version. Increments version on update.
        """
        ...

    async def get_by_id(self, job_id: str) -> AIJob | None:
        """Retrieve an AI Job by its unique ID."""
        ...

    async def get_by_key(self, idempotency_key: str) -> AIJob | None:
        """Retrieve an AI Job by its idempotency key."""
        ...
