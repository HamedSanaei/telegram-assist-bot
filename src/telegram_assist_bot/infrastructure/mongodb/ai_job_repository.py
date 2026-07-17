"""MongoDB implementation of the AIJobRepository port."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Final

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ports import (
    AIJobConcurrencyConflictError,
    AIJobNotFoundError,
    AIJobRepository,
    AIJobRepositoryError,
    EnqueueJobOutcome,
    EnqueueJobResult,
)
from telegram_assist_bot.domain.ai_job import AIJob, AIJobStatus

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

_DUPLICATE_KEY_ERROR_CODE: Final[int] = 11000


def _floor_to_millisecond(value: datetime) -> tuple[datetime, int]:
    """Split a UTC datetime into BSON milliseconds and its lost remainder."""
    # Ensure it's in UTC
    utc_val = value.astimezone(UTC)
    remainder = utc_val.microsecond % 1000
    return utc_val.replace(microsecond=utc_val.microsecond - remainder), remainder


def _restore_floor_datetime(value: datetime, remainder: int) -> datetime:
    """Reconstruct a microsecond timestamp from BSON datetime and remainder."""
    return value.replace(tzinfo=UTC) + timedelta(microseconds=remainder)


def ai_job_to_document(job: AIJob) -> dict[str, Any]:
    """Map an AIJob aggregate to a MongoDB document."""
    next_run_floor, next_run_rem = _floor_to_millisecond(job.next_run_at)

    created_at = job.created_at or datetime.now(UTC)
    created_floor, created_rem = _floor_to_millisecond(created_at)

    updated_at = job.updated_at or datetime.now(UTC)
    updated_floor, updated_rem = _floor_to_millisecond(updated_at)

    lease_expires_floor = None
    lease_expires_rem = 0
    if job.lease_expires_at:
        lease_expires_floor, lease_expires_rem = _floor_to_millisecond(
            job.lease_expires_at
        )

    return {
        "_id": job.job_id,
        "post_id": job.post_id,
        "task_type": job.task_type,
        "prompt_version": job.prompt_version,
        "schema_version": job.schema_version,
        "idempotency_key": job.idempotency_key,
        "status": str(job.status),
        "priority": job.priority,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "next_run_at": next_run_floor,
        "next_run_at_rem": next_run_rem,
        "lease_owner": job.lease_owner,
        "lease_expires_at": lease_expires_floor,
        "lease_expires_at_rem": lease_expires_rem,
        "result": job.result,
        "normalized_result": job.normalized_result,
        "semantic_candidate_results": job.semantic_candidate_results,
        "last_error": job.last_error,
        "created_at": created_floor,
        "created_at_rem": created_rem,
        "updated_at": updated_floor,
        "updated_at_rem": updated_rem,
        "version": job.version,
        "attempts_history": job.attempts_history,
        "attempted_candidates_count": job.attempted_candidates_count,
        "retry_count": job.retry_count,
        "fallback_count": job.fallback_count,
        "safe_last_failure_code": job.safe_last_failure_code,
    }


def ai_job_from_document(doc: dict[str, Any]) -> AIJob:
    """Map a MongoDB document to an AIJob aggregate."""
    next_run_at = _restore_floor_datetime(
        doc["next_run_at"], doc.get("next_run_at_rem", 0)
    )
    created_at = _restore_floor_datetime(
        doc["created_at"], doc.get("created_at_rem", 0)
    )
    updated_at = _restore_floor_datetime(
        doc["updated_at"], doc.get("updated_at_rem", 0)
    )

    lease_expires_at = None
    if doc.get("lease_expires_at") is not None:
        lease_expires_at = _restore_floor_datetime(
            doc["lease_expires_at"], doc.get("lease_expires_at_rem", 0)
        )

    return AIJob(
        job_id=doc["_id"],
        post_id=doc["post_id"],
        task_type=doc["task_type"],
        prompt_version=doc["prompt_version"],
        schema_version=doc["schema_version"],
        idempotency_key=doc["idempotency_key"],
        status=AIJobStatus(doc["status"]),
        priority=doc["priority"],
        attempts=doc["attempts"],
        max_attempts=doc["max_attempts"],
        next_run_at=next_run_at,
        lease_owner=doc.get("lease_owner"),
        lease_expires_at=lease_expires_at,
        result=doc.get("result"),
        normalized_result=doc.get("normalized_result"),
        semantic_candidate_results=doc.get("semantic_candidate_results"),
        last_error=doc.get("last_error"),
        created_at=created_at,
        updated_at=updated_at,
        version=doc["version"],
        attempts_history=doc.get("attempts_history"),
        attempted_candidates_count=doc.get("attempted_candidates_count"),
        retry_count=doc.get("retry_count"),
        fallback_count=doc.get("fallback_count"),
        safe_last_failure_code=doc.get("safe_last_failure_code"),
    )


class MongoAIJobRepository(AIJobRepository):
    """MongoDB implementation of the AIJobRepository protocol."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Initialize with MongoDB collection."""
        self._collection = collection

    async def enqueue(self, job: AIJob) -> EnqueueJobResult:
        """Idempotently insert an AI Job."""
        doc = ai_job_to_document(job)
        try:
            await self._collection.insert_one(doc)
            return EnqueueJobResult(outcome=EnqueueJobOutcome.CREATED, job=job)
        except DuplicateKeyError as e:
            # If duplicates on _id or idempotency_key
            existing_doc = await self._collection.find_one(
                {"idempotency_key": job.idempotency_key}
            )
            if existing_doc is None:
                # Corner case where _id conflicted but key did not,
                # or concurrent deletion
                raise AIJobRepositoryError(
                    "Concurrent write collision on job_id"
                ) from e
            existing_job = ai_job_from_document(existing_doc)
            return EnqueueJobResult(
                outcome=EnqueueJobOutcome.ALREADY_EXISTS, job=existing_job
            )
        except PyMongoError as e:
            raise AIJobRepositoryError("Database error during enqueue") from e

    async def claim_next_due(
        self,
        owner: str,
        lease_duration_seconds: float,
        as_of: datetime,
    ) -> AIJob | None:
        """Atomically claim the next eligible due job."""
        utc_as_of = as_of.astimezone(UTC)
        query = {
            "$or": [
                {
                    "status": {
                        "$in": [
                            str(AIJobStatus.PENDING),
                            str(AIJobStatus.WAITING_FOR_RETRY),
                        ]
                    },
                    "next_run_at": {"$lte": utc_as_of},
                },
                {
                    "status": str(AIJobStatus.PROCESSING),
                    "lease_expires_at": {"$lt": utc_as_of},
                },
            ]
        }

        lease_expires = utc_as_of + timedelta(seconds=lease_duration_seconds)
        update = {
            "$set": {
                "status": str(AIJobStatus.PROCESSING),
                "lease_owner": owner,
                "lease_expires_at": lease_expires,
                "updated_at": utc_as_of,
            },
            "$inc": {
                "attempts": 1,
                "version": 1,
            },
        }

        try:
            doc = await self._collection.find_one_and_update(
                query,
                update,
                sort=[
                    ("priority", DESCENDING),
                    ("next_run_at", ASCENDING),
                    ("created_at", ASCENDING),
                ],
                return_document=ReturnDocument.AFTER,
            )
            if doc is None:
                return None
            return ai_job_from_document(doc)
        except PyMongoError as e:
            raise AIJobRepositoryError("Database error during claim") from e

    async def update(self, job: AIJob) -> None:
        """Update an AI Job using optimistic concurrency control."""
        doc = ai_job_to_document(job)
        try:
            # We match by job_id and the PREVIOUS version (job.version - 1)
            result = await self._collection.update_one(
                {"_id": job.job_id, "version": job.version - 1},
                {"$set": doc},
            )
            if result.matched_count == 0:
                existing = await self._collection.find_one({"_id": job.job_id})
                if existing is None:
                    raise AIJobNotFoundError(f"AI Job {job.job_id} not found")
                raise AIJobConcurrencyConflictError(
                    f"Concurrency conflict for AI Job {job.job_id}: "
                    f"expected version {job.version - 1}, "
                    f"found {existing.get('version')}"
                )
        except PyMongoError as e:
            raise AIJobRepositoryError("Database error during update") from e

    async def get_by_id(self, job_id: str) -> AIJob | None:
        """Retrieve an AI Job by its unique ID."""
        try:
            doc = await self._collection.find_one({"_id": job_id})
            return ai_job_from_document(doc) if doc else None
        except PyMongoError as e:
            raise AIJobRepositoryError("Database error during get_by_id") from e

    async def get_by_key(self, idempotency_key: str) -> AIJob | None:
        """Retrieve an AI Job by its idempotency key."""
        try:
            doc = await self._collection.find_one({"idempotency_key": idempotency_key})
            return ai_job_from_document(doc) if doc else None
        except PyMongoError as e:
            raise AIJobRepositoryError("Database error during get_by_key") from e


async def initialize_ai_job_indexes(
    collection: AsyncCollection[MongoDocument],
) -> None:
    """Initialize the required indexes for the AI Jobs collection."""
    try:
        await collection.create_index(
            [("idempotency_key", 1)],
            name="uq_ai_jobs_idempotency_key_v1",
            unique=True,
        )
        await collection.create_index(
            [
                ("status", 1),
                ("priority", -1),
                ("next_run_at", 1),
                ("created_at", 1),
            ],
            name="idx_ai_jobs_claim_v1",
        )
        await collection.create_index(
            [
                ("status", 1),
                ("lease_expires_at", 1),
            ],
            name="idx_ai_jobs_lease_v1",
        )
    except PyMongoError as e:
        raise AIJobRepositoryError("Database error during index initialization") from e
