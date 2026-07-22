"""MongoDB immutable and idempotent sanitized AI audit adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditRepository,
    AIAuditRepositoryError,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )


def ai_audit_event_to_document(event: AIAuditEvent) -> dict[str, Any]:
    """Serialize only the allowlisted safe audit contract."""
    return {
        "_id": event.event_id,
        "event_id": event.event_id,
        "event_type": event.event_type.value,
        "job_id": event.job_id,
        "post_id": event.post_id,
        "task_type": event.task_type.value,
        "provider_name": event.provider_name,
        "model_name": event.model_name,
        "prompt_version": event.prompt_version,
        "schema_version": event.schema_version,
        "sequence_number": event.sequence_number,
        "attempt_number": event.attempt_number,
        "retry_count": event.retry_count,
        "fallback_count": event.fallback_count,
        "event_success": event.success,
        "failure_category": event.failure_category,
        "http_status": event.http_status,
        "latency_seconds": event.latency_seconds,
        "input_tokens": event.input_tokens,
        "output_tokens": event.output_tokens,
        "cache_hit": event.cache_hit,
        "occurred_at": event.occurred_at,
        "expires_at": event.expires_at,
        "audit_schema_version": event.audit_schema_version,
    }


class MongoAIAuditRepository(AIAuditRepository):
    """Append immutable events while treating duplicate delivery idempotently."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Initialize the isolated audit collection adapter."""
        self._collection = collection

    async def append(self, event: AIAuditEvent) -> bool:
        """Insert one immutable event or return false for its stable duplicate."""
        try:
            await self._collection.insert_one(ai_audit_event_to_document(event))
            return True
        except DuplicateKeyError:
            return False
        except PyMongoError:
            raise AIAuditRepositoryError("ai_audit_append_failed") from None


async def initialize_ai_audit_indexes(
    collection: AsyncCollection[MongoDocument],
) -> None:
    """Create stable identity, query and optional-document TTL indexes."""
    try:
        await collection.create_index(
            [("event_id", ASCENDING)],
            name="uq_ai_audit_event_id_v1",
            unique=True,
        )
        await collection.create_index(
            [("job_id", ASCENDING), ("occurred_at", ASCENDING)],
            name="ix_ai_audit_job_time_v1",
        )
        await collection.create_index(
            [
                ("task_type", ASCENDING),
                ("provider_name", ASCENDING),
                ("occurred_at", DESCENDING),
            ],
            name="ix_ai_audit_task_provider_time_v1",
        )
        await collection.create_index(
            [("expires_at", ASCENDING)],
            name="ttl_ai_audit_expires_at_v1",
            expireAfterSeconds=0,
        )
    except PyMongoError:
        raise AIAuditRepositoryError("ai_audit_index_initialization_failed") from None


__all__ = (
    "MongoAIAuditRepository",
    "ai_audit_event_to_document",
    "initialize_ai_audit_indexes",
)
