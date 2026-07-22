"""MongoDB integration tests for isolated AI cache, audit and metrics."""

# ruff: noqa: RUF001

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.application.ai.cache_key import (
    AICacheIdentity,
    build_ai_cache_identity,
)
from telegram_assist_bot.application.ai.contracts import AIResult, AITaskType
from telegram_assist_bot.application.ai.schemas import AdvertisementDetectionContext
from telegram_assist_bot.application.ports.ai_audit_repository import (
    AIAuditEvent,
    AIAuditEventType,
)
from telegram_assist_bot.application.ports.ai_cache_repository import AICacheEntry
from telegram_assist_bot.application.ports.provider_metrics_repository import (
    ProviderMetricDelta,
)
from telegram_assist_bot.infrastructure.mongodb.ai_audit_repository import (
    MongoAIAuditRepository,
    initialize_ai_audit_indexes,
)
from telegram_assist_bot.infrastructure.mongodb.ai_cache_repository import (
    MongoAICacheRepository,
    initialize_ai_cache_indexes,
)
from telegram_assist_bot.infrastructure.mongodb.provider_metrics_repository import (
    MongoProviderMetricsRepository,
    initialize_provider_metrics_indexes,
)

if TYPE_CHECKING:
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def _identity() -> AICacheIdentity:
    return build_ai_cache_identity(
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        request_context=AdvertisementDetectionContext(text="متن\u200cآزمایشی 🙂"),
        prompt_version="1",
        schema_version="1",
        language="fa",
    )


def _result(provider: str = "provider") -> AIResult:
    return AIResult(
        success=True,
        task_type=AITaskType.ADVERTISEMENT_DETECTION,
        provider_name=provider,
        model_name="model",
        result={
            "is_advertisement": False,
            "confidence": 0.9,
            "reason": "synthetic",
        },
        confidence=0.9,
        reason="synthetic",
        prompt_version="1",
        schema_version="1",
        attempt_number=1,
        fallback_count=0,
        latency=None,
        input_tokens=None,
        output_tokens=None,
    )


def test_cache_indexes_expiry_validation_and_first_valid_write_wins(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        collection = client[mongodb_test_settings.database_name]["ai_result_cache"]
        repository = MongoAICacheRepository(collection)
        try:
            await initialize_ai_cache_indexes(collection)
            identity = _identity()
            entries = [
                AICacheEntry(
                    identity=identity,
                    result=_result(f"provider-{index}"),
                    created_at=NOW,
                    expires_at=NOW + timedelta(minutes=5),
                )
                for index in range(8)
            ]
            writes = await asyncio.gather(
                *(repository.put_if_absent(entry) for entry in entries)
            )
            assert sum(item.created for item in writes) == 1
            stored = await repository.get(identity, as_of=NOW)
            assert stored is not None
            assert all(item.entry.result == stored.result for item in writes)

            assert (
                await repository.get(identity, as_of=NOW + timedelta(minutes=5)) is None
            )
            indexes = await collection.index_information()
            assert indexes["uq_ai_result_cache_key_v1"]["unique"] is True
            assert (
                indexes["ttl_ai_result_cache_expires_at_v1"]["expireAfterSeconds"] == 0
            )

            await collection.update_one(
                {"_id": identity.cache_key},
                {"$set": {"result": {"success": True}}},
            )
            assert await repository.get(identity, as_of=NOW) is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_audit_is_immutable_idempotent_retained_and_redacted(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        collection = client[mongodb_test_settings.database_name]["ai_audit_events"]
        repository = MongoAIAuditRepository(collection)
        try:
            await initialize_ai_audit_indexes(collection)
            event = AIAuditEvent(
                event_id="stable-event",
                event_type=AIAuditEventType.PROVIDER_ATTEMPT,
                job_id="job",
                post_id="post",
                task_type=AITaskType.ADVERTISEMENT_DETECTION,
                prompt_version="1",
                schema_version="1",
                occurred_at=NOW,
                provider_name="provider",
                model_name="model",
                success=True,
                expires_at=NOW + timedelta(days=1),
            )
            assert await repository.append(event) is True
            assert await repository.append(event) is False
            document = await collection.find_one({"_id": "stable-event"})
            assert document is not None
            serialized = json.dumps(document, ensure_ascii=False, default=str)
            for forbidden in (
                "Bearer secret",
                "Authorization",
                "متن خصوصی",
                "person@example.com",
                "raw_response",
            ):
                assert forbidden not in serialized
            indexes = await collection.index_information()
            assert indexes["uq_ai_audit_event_id_v1"]["unique"] is True
            assert indexes["ttl_ai_audit_expires_at_v1"]["expireAfterSeconds"] == 0
        finally:
            await client.close()

    asyncio.run(scenario())


def test_metrics_concurrent_increments_are_lossless_and_independent(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        collection = client[mongodb_test_settings.database_name]["ai_provider_metrics"]
        repository = MongoProviderMetricsRepository(collection)
        try:
            await initialize_provider_metrics_indexes(collection)
            delta = ProviderMetricDelta(
                request_count=1,
                success_count=1,
                input_tokens=2,
                output_tokens=3,
                total_tokens=5,
                cumulative_latency_seconds=0.25,
                latency_sample_count=1,
                last_success_at=NOW,
            )
            await asyncio.gather(
                *(repository.increment("provider", "model", delta) for _ in range(50))
            )
            metrics = await repository.get("provider", "model")
            assert metrics is not None
            assert metrics.request_count == 50
            assert metrics.success_count == 50
            assert metrics.total_tokens == 250
            assert metrics.average_latency_seconds == 0.25

            await repository.increment("provider", "other-model", delta)
            other = await repository.get("provider", "other-model")
            assert other is not None
            assert other.request_count == 1
            indexes = await collection.index_information()
            assert indexes["uq_ai_provider_metrics_identity_v1"]["unique"] is True
        finally:
            await client.close()

    asyncio.run(scenario())


def test_metrics_legacy_document_uses_safe_defaults(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
            mongodb_test_settings.uri, tz_aware=True
        )
        collection = client[mongodb_test_settings.database_name]["ai_provider_metrics"]
        repository = MongoProviderMetricsRepository(collection)
        try:
            await collection.insert_one(
                {"provider_name": "legacy", "model_name": "model"}
            )
            metrics = await repository.get("legacy", "model")
            assert metrics is not None
            assert metrics.request_count == 0
            assert metrics.average_latency_seconds is None
            assert metrics.last_success_at is None
        finally:
            await client.close()

    asyncio.run(scenario())
