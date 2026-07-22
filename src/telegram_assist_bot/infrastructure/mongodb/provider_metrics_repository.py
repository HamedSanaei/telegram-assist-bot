"""MongoDB atomic cumulative Provider/Model metrics adapter."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ports.provider_metrics_repository import (
    ProviderMetricDelta,
    ProviderMetrics,
    ProviderMetricsRepository,
    ProviderMetricsRepositoryError,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

_COUNTER_FIELDS = (
    "request_count",
    "success_count",
    "failure_count",
    "timeout_count",
    "rate_limit_count",
    "invalid_response_count",
    "fallback_participation_count",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cumulative_latency_seconds",
    "latency_sample_count",
)


def provider_metrics_from_document(document: dict[str, Any]) -> ProviderMetrics:
    """Load old metrics documents with non-negative zero/None defaults."""
    return ProviderMetrics(
        provider_name=str(document["provider_name"]),
        model_name=str(document["model_name"]),
        request_count=max(0, int(document.get("request_count", 0))),
        success_count=max(0, int(document.get("success_count", 0))),
        failure_count=max(0, int(document.get("failure_count", 0))),
        timeout_count=max(0, int(document.get("timeout_count", 0))),
        rate_limit_count=max(0, int(document.get("rate_limit_count", 0))),
        invalid_response_count=max(0, int(document.get("invalid_response_count", 0))),
        fallback_participation_count=max(
            0, int(document.get("fallback_participation_count", 0))
        ),
        input_tokens=max(0, int(document.get("input_tokens", 0))),
        output_tokens=max(0, int(document.get("output_tokens", 0))),
        total_tokens=max(0, int(document.get("total_tokens", 0))),
        cumulative_latency_seconds=max(
            0.0, float(document.get("cumulative_latency_seconds", 0.0))
        ),
        latency_sample_count=max(0, int(document.get("latency_sample_count", 0))),
        last_success_at=document.get("last_success_at"),
        last_error_at=document.get("last_error_at"),
    )


class MongoProviderMetricsRepository(ProviderMetricsRepository):
    """Apply concurrent cumulative increments without storing derived averages."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Initialize the isolated metrics collection adapter."""
        self._collection = collection

    async def increment(
        self, provider_name: str, model_name: str, delta: ProviderMetricDelta
    ) -> ProviderMetrics:
        """Atomically increment one exact Provider/Model aggregate."""
        increments = {field: getattr(delta, field) for field in _COUNTER_FIELDS}
        update: dict[str, Any] = {
            "$setOnInsert": {
                "provider_name": provider_name,
                "model_name": model_name,
                "metrics_schema_version": 1,
            },
            "$inc": increments,
        }
        latest: dict[str, Any] = {}
        if delta.last_success_at is not None:
            latest["last_success_at"] = delta.last_success_at
        if delta.last_error_at is not None:
            latest["last_error_at"] = delta.last_error_at
        if latest:
            update["$max"] = latest
        identity = {"provider_name": provider_name, "model_name": model_name}
        try:
            try:
                document = await self._collection.find_one_and_update(
                    identity,
                    update,
                    upsert=True,
                    return_document=ReturnDocument.AFTER,
                )
            except DuplicateKeyError:
                document = await self._collection.find_one_and_update(
                    identity,
                    update,
                    upsert=False,
                    return_document=ReturnDocument.AFTER,
                )
        except PyMongoError:
            raise ProviderMetricsRepositoryError(
                "ai_provider_metrics_increment_failed"
            ) from None
        if document is None:
            raise ProviderMetricsRepositoryError("ai_provider_metrics_missing")
        return provider_metrics_from_document(document)

    async def get(self, provider_name: str, model_name: str) -> ProviderMetrics | None:
        """Read one aggregate with compatibility defaults."""
        try:
            document = await self._collection.find_one(
                {"provider_name": provider_name, "model_name": model_name}
            )
        except PyMongoError:
            raise ProviderMetricsRepositoryError(
                "ai_provider_metrics_read_failed"
            ) from None
        return provider_metrics_from_document(document) if document else None


async def initialize_provider_metrics_indexes(
    collection: AsyncCollection[MongoDocument],
) -> None:
    """Create the unique Provider/Model aggregate identity index."""
    try:
        await collection.create_index(
            [("provider_name", ASCENDING), ("model_name", ASCENDING)],
            name="uq_ai_provider_metrics_identity_v1",
            unique=True,
        )
    except PyMongoError:
        raise ProviderMetricsRepositoryError(
            "ai_provider_metrics_index_initialization_failed"
        ) from None


__all__ = (
    "MongoProviderMetricsRepository",
    "initialize_provider_metrics_indexes",
    "provider_metrics_from_document",
)
