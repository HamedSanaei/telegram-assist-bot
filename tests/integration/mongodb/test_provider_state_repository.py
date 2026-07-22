"""MongoDB integration tests for atomic provider health reservations."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pymongo import AsyncMongoClient

from telegram_assist_bot.domain.ai.provider_health import (
    CircuitState,
    ProviderAttemptOutcome,
    ProviderFailureCategory,
    ReservationKind,
)
from telegram_assist_bot.infrastructure.mongodb.provider_state_repository import (
    MongoProviderStateRepository,
    initialize_provider_state_indexes,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.application.ports.provider_state_repository import (
        ProviderReservationResult,
    )
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )
    from tests.integration.infrastructure.persistence.conftest import MongoTestSettings

NOW = datetime(2026, 7, 18, 10, 0, tzinfo=UTC)


async def _repository(
    settings: MongoTestSettings,
) -> tuple[
    AsyncMongoClient[dict[str, object]],
    MongoProviderStateRepository,
    AsyncCollection[MongoDocument],
]:
    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.uri,
        tz_aware=True,
    )
    collection = client[settings.database_name]["provider_state"]
    await initialize_provider_state_indexes(collection)
    return client, MongoProviderStateRepository(collection), collection


async def _reserve(
    repository: MongoProviderStateRepository,
    owner: str,
    *,
    now: datetime = NOW,
    concurrency_limit: int = 2,
) -> ProviderReservationResult:
    return await repository.reserve(
        "provider",
        "model",
        owner,
        now,
        now + timedelta(seconds=30),
        concurrency_limit=concurrency_limit,
        request_window_seconds=60,
        request_limit=100,
    )


def test_concurrent_capacity_supports_multiple_independent_reservations(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, _ = await _repository(mongodb_test_settings)
        try:
            results = await asyncio.gather(
                *(_reserve(repository, f"owner-{index}") for index in range(8))
            )
            granted = [item.reservation for item in results if item.reservation]
            assert len(granted) == 2
            assert len({item.reservation_id for item in granted}) == 2
            assert len({item.owner_id for item in granted}) == 2
            state = await repository.get_or_create("provider", "model")
            assert len(state.active_reservations) == 2
        finally:
            await client.close()

    asyncio.run(scenario())


def test_release_ownership_and_idempotency_never_make_counters_negative(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, _ = await _repository(mongodb_test_settings)
        try:
            result = await _reserve(repository, "owner")
            assert result.reservation is not None
            token = result.reservation.reservation_id
            wrong_owner = await repository.record(
                "provider",
                "model",
                token,
                "other-owner",
                ProviderAttemptOutcome.succeeded(),
                NOW,
                failure_threshold=2,
                open_seconds=10,
                fallback_cooldown_seconds=None,
            )
            assert len(wrong_owner.active_reservations) == 1
            released = await repository.record(
                "provider",
                "model",
                token,
                "owner",
                ProviderAttemptOutcome.succeeded(),
                NOW,
                failure_threshold=2,
                open_seconds=10,
                fallback_cooldown_seconds=None,
            )
            repeated = await repository.record(
                "provider",
                "model",
                token,
                "owner",
                ProviderAttemptOutcome.succeeded(),
                NOW,
                failure_threshold=2,
                open_seconds=10,
                fallback_cooldown_seconds=None,
            )
            assert released.active_reservations == ()
            assert repeated.failure_count == 0
            assert repeated.request_count >= 0
        finally:
            await client.close()

    asyncio.run(scenario())


def test_expired_reservation_is_reclaimed_without_deleting_health_state(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, collection = await _repository(mongodb_test_settings)
        try:
            first = await _reserve(repository, "old-owner")
            assert first.reservation is not None
            await collection.update_one(
                {"provider_name": "provider", "model_name": "model"},
                {"$set": {"failure_count": 1}},
            )
            later = NOW + timedelta(seconds=31)
            second = await _reserve(
                repository,
                "new-owner",
                now=later,
                concurrency_limit=1,
            )
            assert second.reservation is not None
            state = await repository.get_or_create("provider", "model")
            assert state.failure_count == 1
            assert [item.owner_id for item in state.active_reservations] == [
                "new-owner"
            ]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_state_survives_repository_restart_and_index_has_no_ttl(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, collection = await _repository(mongodb_test_settings)
        try:
            await _reserve(repository, "owner")
            restarted = MongoProviderStateRepository(collection)
            state = await restarted.get_or_create("provider", "model")
            assert state.request_count == 1
            assert len(state.active_reservations) == 1
            indexes = await collection.index_information()
            identity = indexes["uq_provider_state_identity_v1"]
            assert identity["unique"] is True
            assert all("expireAfterSeconds" not in value for value in indexes.values())
        finally:
            await client.close()

    asyncio.run(scenario())


def test_exact_circuit_transitions_and_single_half_open_probe(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, collection = await _repository(mongodb_test_settings)
        try:
            first = await _reserve(repository, "failure-one")
            assert first.reservation is not None
            failure = ProviderAttemptOutcome(
                success=False,
                failure_category=ProviderFailureCategory.TRANSIENT,
                health_failure=True,
            )
            state = await repository.record(
                "provider",
                "model",
                first.reservation.reservation_id,
                "failure-one",
                failure,
                NOW,
                failure_threshold=1,
                open_seconds=10,
                fallback_cooldown_seconds=None,
            )
            assert state.circuit_state is CircuitState.OPEN
            blocked = await _reserve(
                repository,
                "early",
                now=NOW + timedelta(seconds=9),
            )
            assert blocked.reservation is None

            exact = NOW + timedelta(seconds=10)
            probes = await asyncio.gather(
                *(
                    _reserve(repository, f"probe-{index}", now=exact)
                    for index in range(5)
                )
            )
            granted = [item.reservation for item in probes if item.reservation]
            assert len(granted) == 1
            assert granted[0].kind is ReservationKind.HALF_OPEN_PROBE
            probe = granted[0]
            closed = await repository.record(
                "provider",
                "model",
                probe.reservation_id,
                probe.owner_id,
                ProviderAttemptOutcome.succeeded(),
                exact,
                failure_threshold=1,
                open_seconds=10,
                fallback_cooldown_seconds=None,
            )
            assert closed.circuit_state is CircuitState.CLOSED
            assert closed.failure_count == 0
            assert await collection.count_documents({}) == 1
        finally:
            await client.close()

    asyncio.run(scenario())


def test_rate_limit_cooldown_and_non_health_failures(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, _ = await _repository(mongodb_test_settings)
        try:
            first = await _reserve(repository, "rate-owner")
            assert first.reservation is not None
            rate = ProviderAttemptOutcome(
                success=False,
                failure_category=ProviderFailureCategory.RATE_LIMIT,
                health_failure=True,
                rate_limited=True,
                retry_after_seconds=20,
            )
            state = await repository.record(
                "provider",
                "model",
                first.reservation.reservation_id,
                "rate-owner",
                rate,
                NOW,
                failure_threshold=5,
                open_seconds=10,
                fallback_cooldown_seconds=None,
            )
            assert state.cooldown_until == NOW + timedelta(seconds=20)
            blocked = await _reserve(
                repository,
                "blocked",
                now=NOW + timedelta(seconds=1),
            )
            assert blocked.reservation is None

            other = await repository.reserve(
                "provider",
                "other-model",
                "auth-owner",
                NOW,
                NOW + timedelta(seconds=30),
                concurrency_limit=1,
                request_window_seconds=60,
                request_limit=10,
            )
            assert other.reservation is not None
            auth_state = await repository.record(
                "provider",
                "other-model",
                other.reservation.reservation_id,
                "auth-owner",
                ProviderAttemptOutcome(
                    success=False,
                    failure_category=ProviderFailureCategory.AUTHORIZATION,
                ),
                NOW,
                failure_threshold=1,
                open_seconds=10,
                fallback_cooldown_seconds=30,
            )
            assert auth_state.failure_count == 0
            assert auth_state.cooldown_until is None
        finally:
            await client.close()

    asyncio.run(scenario())


def test_legacy_document_receives_safe_compatibility_defaults(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        client, repository, collection = await _repository(mongodb_test_settings)
        try:
            await collection.insert_one(
                {"provider_name": "legacy", "model_name": "model"}
            )
            state = await repository.get_or_create("legacy", "model")
            assert state.circuit_state is CircuitState.CLOSED
            assert state.failure_count == 0
            assert state.request_count == 0
            assert state.active_reservations == ()
            assert state.version == 0
        finally:
            await client.close()

    asyncio.run(scenario())
