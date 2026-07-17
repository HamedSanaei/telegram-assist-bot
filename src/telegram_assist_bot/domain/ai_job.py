"""Domain entities and value objects for the AI Job Queue."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any


class AIJobStatus(StrEnum):
    """Lifecycle statuses for a durable AI Job."""

    PENDING = "Pending"
    PROCESSING = "Processing"
    WAITING_FOR_RETRY = "WaitingForRetry"
    COMPLETED = "Completed"
    ALL_PROVIDERS_FAILED = "AllProvidersFailed"
    CANCELLED = "Cancelled"
    EXPIRED = "Expired"


class AIJobPriority(int):
    """Priority levels for AI Jobs, where higher values take precedence."""

    HIGH = 30
    MEDIUM = 20
    LOW = 10


@dataclass(frozen=True)
class AIJob:
    """Immutable aggregate representing a durable AI Job."""

    job_id: str
    post_id: str
    task_type: str
    prompt_version: str
    schema_version: str
    idempotency_key: str
    status: AIJobStatus
    priority: int
    attempts: int
    max_attempts: int
    next_run_at: datetime
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    result: dict[str, Any] | None = None
    normalized_result: dict[str, Any] | None = None
    last_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    version: int = 0
    attempts_history: list[dict[str, Any]] | None = None
    attempted_candidates_count: int | None = None
    retry_count: int | None = None
    fallback_count: int | None = None
    safe_last_failure_code: str | None = None

    @classmethod
    def create(
        cls,
        job_id: str,
        post_id: str,
        task_type: str,
        prompt_version: str,
        schema_version: str,
        priority: int,
        max_attempts: int = 3,
        created_at: datetime | None = None,
    ) -> AIJob:
        """Create a new AI Job in Pending state."""
        if (
            not job_id
            or not post_id
            or not task_type
            or not prompt_version
            or not schema_version
        ):
            raise ValueError("Missing required fields for AIJob creation")

        now = created_at or datetime.now(UTC)
        idempotency_key = f"{post_id}:{task_type}:{prompt_version}:{schema_version}"

        return cls(
            job_id=job_id,
            post_id=post_id,
            task_type=task_type,
            prompt_version=prompt_version,
            schema_version=schema_version,
            idempotency_key=idempotency_key,
            status=AIJobStatus.PENDING,
            priority=priority,
            attempts=0,
            max_attempts=max_attempts,
            next_run_at=now,
            created_at=now,
            updated_at=now,
            version=0,
        )

    def claim(
        self, owner: str, lease_duration_seconds: float, claimed_at: datetime
    ) -> AIJob:
        """Transition the job to Processing state under an owner lease."""
        if self.status not in (
            AIJobStatus.PENDING,
            AIJobStatus.WAITING_FOR_RETRY,
            AIJobStatus.PROCESSING,
        ):
            raise ValueError(f"Cannot claim job in status {self.status}")

        # If lease is active and not expired, cannot claim
        if (
            self.status == AIJobStatus.PROCESSING
            and self.lease_expires_at
            and self.lease_expires_at > claimed_at
            and self.lease_owner != owner
        ):
            raise ValueError("Job is currently leased by another owner")

        lease_expires = claimed_at + timedelta(seconds=lease_duration_seconds)
        return replace(
            self,
            status=AIJobStatus.PROCESSING,
            lease_owner=owner,
            lease_expires_at=lease_expires,
            attempts=self.attempts + 1,
            updated_at=claimed_at,
            version=self.version + 1,
        )

    def complete(
        self, owner: str, result: dict[str, Any], completed_at: datetime
    ) -> AIJob:
        """Transition the job to Completed state."""
        if self.status != AIJobStatus.PROCESSING:
            raise ValueError("Can only complete a processing job")
        if self.lease_owner != owner:
            raise ValueError("Only the lease owner can complete the job")
        if self.lease_expires_at and self.lease_expires_at < completed_at:
            raise ValueError("Lease has expired")

        return replace(
            self,
            status=AIJobStatus.COMPLETED,
            lease_owner=None,
            lease_expires_at=None,
            result=result,
            updated_at=completed_at,
            version=self.version + 1,
        )

    def fail(
        self,
        owner: str,
        error: str,
        next_run_delay_seconds: float,
        failed_at: datetime,
    ) -> AIJob:
        """Increment attempts and transition to retry or failure state."""
        if self.status != AIJobStatus.PROCESSING:
            raise ValueError("Can only fail a processing job")
        if self.lease_owner != owner:
            raise ValueError("Only the lease owner can fail the job")

        if self.attempts >= self.max_attempts:
            return replace(
                self,
                status=AIJobStatus.ALL_PROVIDERS_FAILED,
                lease_owner=None,
                lease_expires_at=None,
                last_error=error,
                updated_at=failed_at,
                version=self.version + 1,
            )
        next_run = failed_at + timedelta(seconds=next_run_delay_seconds)
        return replace(
            self,
            status=AIJobStatus.WAITING_FOR_RETRY,
            lease_owner=None,
            lease_expires_at=None,
            last_error=error,
            next_run_at=next_run,
            updated_at=failed_at,
            version=self.version + 1,
        )

    def release(self, owner: str, released_at: datetime) -> AIJob:
        """Gracefully release the lease, returning the job to Pending."""
        if self.status != AIJobStatus.PROCESSING:
            raise ValueError("Can only release a processing job")
        if self.lease_owner != owner:
            raise ValueError("Only the lease owner can release the job")

        return replace(
            self,
            status=AIJobStatus.PENDING,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=released_at,
            version=self.version + 1,
        )

    def cancel(self, cancelled_at: datetime) -> AIJob:
        """Transition the job to Cancelled state."""
        terminal_statuses = (
            AIJobStatus.COMPLETED,
            AIJobStatus.ALL_PROVIDERS_FAILED,
            AIJobStatus.CANCELLED,
            AIJobStatus.EXPIRED,
        )
        if self.status in terminal_statuses:
            raise ValueError(f"Cannot cancel a job in terminal status {self.status}")

        return replace(
            self,
            status=AIJobStatus.CANCELLED,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=cancelled_at,
            version=self.version + 1,
        )

    def expire(self, expired_at: datetime) -> AIJob:
        """Transition the job to Expired state."""
        terminal_statuses = (
            AIJobStatus.COMPLETED,
            AIJobStatus.ALL_PROVIDERS_FAILED,
            AIJobStatus.CANCELLED,
            AIJobStatus.EXPIRED,
        )
        if self.status in terminal_statuses:
            raise ValueError(f"Cannot expire a job in terminal status {self.status}")

        return replace(
            self,
            status=AIJobStatus.EXPIRED,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=expired_at,
            version=self.version + 1,
        )
