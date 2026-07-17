"""Unit tests for the AI Job Domain and Lifecycle Transitions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus


def test_idempotency_key_generation_and_uniqueness() -> None:
    """Verify that idempotency key is deterministic.

    It changes when task/prompt/schema change.
    """
    job1 = AIJob.create(
        job_id="job-1",
        post_id="post-123",
        task_type="advertisement_detection",
        prompt_version="1.0.0",
        schema_version="1",
        priority=30,
    )
    assert job1.idempotency_key == "post-123:advertisement_detection:1.0.0:1"
    assert job1.status == AIJobStatus.PENDING
    assert job1.version == 0
    assert job1.attempts == 0

    # Same parameters -> Same idempotency key
    job2 = AIJob.create(
        job_id="job-2",
        post_id="post-123",
        task_type="advertisement_detection",
        prompt_version="1.0.0",
        schema_version="1",
        priority=20,
    )
    assert job1.idempotency_key == job2.idempotency_key

    # Different prompt version -> Different key
    job3 = AIJob.create(
        job_id="job-3",
        post_id="post-123",
        task_type="advertisement_detection",
        prompt_version="1.0.1",
        schema_version="1",
        priority=30,
    )
    assert job1.idempotency_key != job3.idempotency_key

    # Different schema version -> Different key
    job4 = AIJob.create(
        job_id="job-4",
        post_id="post-123",
        task_type="advertisement_detection",
        prompt_version="1.0.0",
        schema_version="2",
        priority=30,
    )
    assert job1.idempotency_key != job4.idempotency_key


def test_ai_job_creation_validation() -> None:
    """Verify that creation fails if required fields are missing."""
    with pytest.raises(ValueError, match="Missing required fields"):
        AIJob.create("", "post-123", "scoring", "1.0", "1", 10)

    with pytest.raises(ValueError, match="Missing required fields"):
        AIJob.create("job-1", "", "scoring", "1.0", "1", 10)

    with pytest.raises(ValueError, match="Missing required fields"):
        AIJob.create("job-1", "post-123", "", "1.0", "1", 10)


def test_ai_job_claim_lifecycle() -> None:
    """Verify the claim operation transitions state and sets lease correctly."""
    now = datetime.now(UTC)
    job = AIJob.create(
        job_id="job-1",
        post_id="post-1",
        task_type="scoring",
        prompt_version="1.0.0",
        schema_version="1",
        priority=20,
        created_at=now,
    )

    # Claim the job
    claimed_at = now + timedelta(seconds=5)
    claimed_job = job.claim(
        owner="worker-1", lease_duration_seconds=60, claimed_at=claimed_at
    )

    assert claimed_job.status == AIJobStatus.PROCESSING
    assert claimed_job.lease_owner == "worker-1"
    assert claimed_job.lease_expires_at == claimed_at + timedelta(seconds=60)
    assert claimed_job.attempts == 1
    assert claimed_job.version == 1

    # Claiming a claimed job by another worker fails if lease is active
    with pytest.raises(ValueError, match="leased by another owner"):
        claimed_job.claim(
            owner="worker-2",
            lease_duration_seconds=30,
            claimed_at=claimed_at + timedelta(seconds=10),
        )

    # Reclaiming a claimed job by the SAME worker is allowed (extends lease)
    reclaimed_at = claimed_at + timedelta(seconds=10)
    reclaimed_job = claimed_job.claim(
        owner="worker-1", lease_duration_seconds=60, claimed_at=reclaimed_at
    )
    assert reclaimed_job.lease_expires_at == reclaimed_at + timedelta(seconds=60)
    assert reclaimed_job.version == 2
    assert reclaimed_job.attempts == 2

    # Reclaiming an expired lease by another owner is allowed
    expired_at = claimed_job.lease_expires_at + timedelta(seconds=1)
    stolen_job = claimed_job.claim(
        owner="worker-2", lease_duration_seconds=60, claimed_at=expired_at
    )
    assert stolen_job.lease_owner == "worker-2"
    assert stolen_job.attempts == 2


def test_ai_job_complete_lifecycle() -> None:
    """Verify completing a job transitions state and clears lease."""
    now = datetime.now(UTC)
    job = AIJob.create("job-1", "post-1", "scoring", "1.0", "1", 20, created_at=now)
    claimed = job.claim("worker-1", 60, now + timedelta(seconds=5))

    # Complete successfully
    result_data = {"score": 8, "reason": "good"}
    completed = claimed.complete("worker-1", result_data, now + timedelta(seconds=10))

    assert completed.status == AIJobStatus.COMPLETED
    assert completed.lease_owner is None
    assert completed.lease_expires_at is None
    assert completed.result == result_data
    assert completed.version == 2

    # Complete by wrong owner fails
    with pytest.raises(ValueError, match="Only the lease owner"):
        claimed.complete("worker-2", result_data, now + timedelta(seconds=10))

    # Complete after lease expiry fails
    assert claimed.lease_expires_at is not None
    expired_at = claimed.lease_expires_at + timedelta(seconds=1)
    with pytest.raises(ValueError, match="Lease has expired"):
        claimed.complete("worker-1", result_data, expired_at)


def test_ai_job_fail_and_retry_lifecycle() -> None:
    """Verify failing increments attempts, waiting for retry, and final failure."""
    now = datetime.now(UTC)
    job = AIJob.create(
        "job-1", "post-1", "scoring", "1.0", "1", 20, max_attempts=2, created_at=now
    )

    # Attempt 1
    claimed_1 = job.claim("worker-1", 60, now)
    failed_1 = claimed_1.fail(
        "worker-1",
        "Timeout error",
        next_run_delay_seconds=30,
        failed_at=now + timedelta(seconds=10),
    )

    assert failed_1.status == AIJobStatus.WAITING_FOR_RETRY
    assert failed_1.lease_owner is None
    assert failed_1.lease_expires_at is None
    assert failed_1.attempts == 1
    assert failed_1.last_error == "Timeout error"
    assert failed_1.next_run_at == now + timedelta(seconds=40)

    # Attempt 2
    claimed_2 = failed_1.claim("worker-2", 60, now + timedelta(seconds=45))
    assert claimed_2.attempts == 2

    # Fails permanently on attempt 2 (max_attempts = 2)
    failed_2 = claimed_2.fail(
        "worker-2",
        "Fatal rate limit",
        next_run_delay_seconds=30,
        failed_at=now + timedelta(seconds=50),
    )

    assert failed_2.status == AIJobStatus.ALL_PROVIDERS_FAILED
    assert failed_2.lease_owner is None
    assert failed_2.attempts == 2
    assert failed_2.last_error == "Fatal rate limit"


def test_ai_job_release_lifecycle() -> None:
    """Verify releasing returns the job to Pending."""
    now = datetime.now(UTC)
    job = AIJob.create("job-1", "post-1", "scoring", "1.0", "1", 20, created_at=now)
    claimed = job.claim("worker-1", 60, now)

    # Release
    released = claimed.release("worker-1", now + timedelta(seconds=10))
    assert released.status == AIJobStatus.PENDING
    assert released.lease_owner is None
    assert released.lease_expires_at is None
    assert released.version == 2

    # Wrong owner cannot release
    with pytest.raises(ValueError, match="Only the lease owner"):
        claimed.release("worker-2", now)


def test_ai_job_cancellation_and_expiration() -> None:
    """Verify cancelling/expiring works on non-terminal states."""
    now = datetime.now(UTC)
    job = AIJob.create("job-1", "post-1", "scoring", "1.0", "1", 20, created_at=now)

    cancelled = job.cancel(now)
    assert cancelled.status == AIJobStatus.CANCELLED

    # Cannot cancel terminal status
    with pytest.raises(ValueError, match="Cannot cancel a job in terminal status"):
        cancelled.cancel(now)

    # Expiry
    job2 = AIJob.create("job-2", "post-1", "scoring", "1.0", "1", 20, created_at=now)
    expired = job2.expire(now)
    assert expired.status == AIJobStatus.EXPIRED

    with pytest.raises(ValueError, match="Cannot expire a job in terminal status"):
        expired.expire(now)
