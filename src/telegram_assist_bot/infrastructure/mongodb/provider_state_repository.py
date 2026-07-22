"""Atomic MongoDB Provider/Model capacity and health repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ports.provider_state_repository import (
    ProviderReservationResult,
    ProviderStateRepository,
    ProviderStateRepositoryError,
)
from telegram_assist_bot.domain.ai.provider_health import (
    ActiveReservation,
    CircuitState,
    IneligibilityReason,
    ProviderAttemptOutcome,
    ProviderHealth,
    ReservationKind,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )


def _identity(provider_name: str, model_name: str) -> dict[str, str]:
    return {"provider_name": provider_name, "model_name": model_name}


def _reservation_from_document(document: dict[str, Any]) -> ActiveReservation:
    return ActiveReservation(
        reservation_id=str(document["reservation_id"]),
        owner_id=str(document["owner_id"]),
        kind=ReservationKind(document.get("kind", ReservationKind.NORMAL)),
        created_at=document["created_at"],
        expires_at=document["expires_at"],
    )


def provider_health_from_document(document: dict[str, Any]) -> ProviderHealth:
    """Load state with safe defaults for documents created by older versions."""
    return ProviderHealth(
        provider_name=str(document["provider_name"]),
        model_name=str(document["model_name"]),
        circuit_state=CircuitState(document.get("circuit_state", CircuitState.CLOSED)),
        failure_count=max(0, int(document.get("failure_count", 0))),
        open_until=document.get("open_until"),
        cooldown_until=document.get("cooldown_until"),
        request_window_start=document.get("request_window_start"),
        request_count=max(0, int(document.get("request_count", 0))),
        active_reservations=tuple(
            _reservation_from_document(item)
            for item in document.get("active_reservations", [])
        ),
        version=max(0, int(document.get("version", 0))),
    )


def _state_fields(state: ProviderHealth) -> dict[str, Any]:
    return {
        "circuit_state": state.circuit_state.value,
        "failure_count": state.failure_count,
        "open_until": state.open_until,
        "cooldown_until": state.cooldown_until,
        "request_window_start": state.request_window_start,
        "request_count": state.request_count,
        "active_reservations": [
            {
                "reservation_id": item.reservation_id,
                "owner_id": item.owner_id,
                "kind": item.kind.value,
                "created_at": item.created_at,
                "expires_at": item.expires_at,
            }
            for item in state.active_reservations
        ],
        "version": state.version,
    }


def _live_reservations_expression(now: datetime) -> dict[str, object]:
    return {
        "$filter": {
            "input": {"$ifNull": ["$active_reservations", []]},
            "as": "reservation",
            "cond": {"$gt": ["$$reservation.expires_at", now]},
        }
    }


def _no_live_probe_expression(now: datetime) -> dict[str, object]:
    return {
        "$eq": [
            {
                "$size": {
                    "$filter": {
                        "input": _live_reservations_expression(now),
                        "as": "reservation",
                        "cond": {
                            "$eq": [
                                "$$reservation.kind",
                                ReservationKind.HALF_OPEN_PROBE.value,
                            ]
                        },
                    }
                }
            },
            0,
        ]
    }


class MongoProviderStateRepository(ProviderStateRepository):
    """Store persistent health and acquire capacity with one atomic update."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Initialize the repository for the persistent provider-state collection."""
        self._collection = collection

    async def _ensure_document(self, provider_name: str, model_name: str) -> None:
        try:
            await self._collection.update_one(
                _identity(provider_name, model_name),
                {
                    "$setOnInsert": {
                        **_identity(provider_name, model_name),
                        "circuit_state": CircuitState.CLOSED.value,
                        "failure_count": 0,
                        "open_until": None,
                        "cooldown_until": None,
                        "request_window_start": None,
                        "request_count": 0,
                        "active_reservations": [],
                        "version": 0,
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            return
        except PyMongoError:
            raise ProviderStateRepositoryError(
                "Provider-state initialization failed"
            ) from None

    async def get_or_create(
        self, provider_name: str, model_name: str
    ) -> ProviderHealth:
        """Return persistent state with compatibility defaults."""
        await self._ensure_document(provider_name, model_name)
        try:
            document = await self._collection.find_one(
                _identity(provider_name, model_name)
            )
        except PyMongoError:
            raise ProviderStateRepositoryError("Provider-state read failed") from None
        if document is None:
            raise ProviderStateRepositoryError("Provider-state document is unavailable")
        return provider_health_from_document(document)

    async def reserve(
        self,
        provider_name: str,
        model_name: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
        *,
        concurrency_limit: int,
        request_window_seconds: int,
        request_limit: int,
    ) -> ProviderReservationResult:
        """Atomically reclaim leases, enforce capacity and append one reservation."""
        current = now.astimezone(UTC)
        expiry = expires_at.astimezone(UTC)
        await self._ensure_document(provider_name, model_name)
        reservation_id = uuid4().hex
        live = _live_reservations_expression(current)
        window_milliseconds = request_window_seconds * 1000
        circuit = {"$ifNull": ["$circuit_state", CircuitState.CLOSED.value]}
        no_probe = _no_live_probe_expression(current)
        eligibility = {
            "$and": [
                {
                    "$or": [
                        {"$eq": [{"$ifNull": ["$cooldown_until", None]}, None]},
                        {"$lte": ["$cooldown_until", current]},
                    ]
                },
                {
                    "$or": [
                        {"$eq": [circuit, CircuitState.CLOSED.value]},
                        {
                            "$and": [
                                {"$eq": [circuit, CircuitState.OPEN.value]},
                                {"$ne": [{"$ifNull": ["$open_until", None]}, None]},
                                {"$lte": ["$open_until", current]},
                                no_probe,
                            ]
                        },
                        {
                            "$and": [
                                {"$eq": [circuit, CircuitState.HALF_OPEN.value]},
                                no_probe,
                            ]
                        },
                    ]
                },
                {"$lt": [{"$size": live}, concurrency_limit]},
                {
                    "$or": [
                        {
                            "$eq": [
                                {"$ifNull": ["$request_window_start", None]},
                                None,
                            ]
                        },
                        {
                            "$lte": [
                                {
                                    "$add": [
                                        "$request_window_start",
                                        window_milliseconds,
                                    ]
                                },
                                current,
                            ]
                        },
                        {
                            "$lt": [
                                {"$ifNull": ["$request_count", 0]},
                                request_limit,
                            ]
                        },
                    ]
                },
            ]
        }
        reservation_kind = {
            "$cond": [
                {"$eq": [circuit, CircuitState.CLOSED.value]},
                ReservationKind.NORMAL.value,
                ReservationKind.HALF_OPEN_PROBE.value,
            ]
        }
        window_expired = {
            "$or": [
                {"$eq": [{"$ifNull": ["$request_window_start", None]}, None]},
                {
                    "$lte": [
                        {"$add": ["$request_window_start", window_milliseconds]},
                        current,
                    ]
                },
            ]
        }
        pipeline: list[dict[str, object]] = [
            {
                "$set": {
                    "active_reservations": live,
                    "circuit_state": circuit,
                    "failure_count": {"$ifNull": ["$failure_count", 0]},
                    "request_count": {"$ifNull": ["$request_count", 0]},
                    "version": {"$ifNull": ["$version", 0]},
                }
            },
            {
                "$set": {
                    "active_reservations": {
                        "$concatArrays": [
                            "$active_reservations",
                            [
                                {
                                    "reservation_id": reservation_id,
                                    "owner_id": owner_id,
                                    "kind": reservation_kind,
                                    "created_at": current,
                                    "expires_at": expiry,
                                }
                            ],
                        ]
                    },
                    "circuit_state": {
                        "$cond": [
                            {"$eq": [circuit, CircuitState.CLOSED.value]},
                            CircuitState.CLOSED.value,
                            CircuitState.HALF_OPEN.value,
                        ]
                    },
                    "request_window_start": {
                        "$cond": [window_expired, current, "$request_window_start"]
                    },
                    "request_count": {
                        "$cond": [
                            window_expired,
                            1,
                            {"$add": ["$request_count", 1]},
                        ]
                    },
                    "version": {"$add": ["$version", 1]},
                }
            },
        ]
        try:
            document = await self._collection.find_one_and_update(
                {**_identity(provider_name, model_name), "$expr": eligibility},
                pipeline,
                return_document=ReturnDocument.AFTER,
            )
        except PyMongoError:
            raise ProviderStateRepositoryError(
                "Provider-state reservation failed"
            ) from None
        if document is not None:
            stored_reservations = cast(
                "list[dict[str, Any]]",
                document["active_reservations"],
            )
            reservation = next(
                _reservation_from_document(item)
                for item in stored_reservations
                if item["reservation_id"] == reservation_id
            )
            return ProviderReservationResult(reservation=reservation)
        state = await self.get_or_create(provider_name, model_name)
        decision = state.eligibility(
            current,
            concurrency_limit=concurrency_limit,
            request_window_seconds=request_window_seconds,
            request_limit=request_limit,
        )
        return ProviderReservationResult(
            reservation=None,
            reason=decision.reason or IneligibilityReason.CONCURRENCY_LIMIT,
            next_eligible_at=decision.next_eligible_at,
        )

    async def record(
        self,
        provider_name: str,
        model_name: str,
        reservation_id: str,
        owner_id: str,
        outcome: ProviderAttemptOutcome,
        now: datetime,
        *,
        failure_threshold: int,
        open_seconds: int,
        fallback_cooldown_seconds: int | None,
    ) -> ProviderHealth:
        """Release only the matching owner reservation with optimistic CAS."""
        for _ in range(5):
            state = await self.get_or_create(provider_name, model_name)
            updated = state.record_outcome(
                reservation_id,
                owner_id,
                outcome,
                now,
                failure_threshold=failure_threshold,
                open_seconds=open_seconds,
                fallback_cooldown_seconds=fallback_cooldown_seconds,
            )
            if updated is state:
                return state
            try:
                result = await self._collection.update_one(
                    {
                        **_identity(provider_name, model_name),
                        "version": state.version,
                        "active_reservations": {
                            "$elemMatch": {
                                "reservation_id": reservation_id,
                                "owner_id": owner_id,
                            }
                        },
                    },
                    {"$set": _state_fields(updated)},
                )
            except PyMongoError:
                raise ProviderStateRepositoryError(
                    "Provider-state outcome recording failed"
                ) from None
            if result.modified_count == 1:
                return updated
        raise ProviderStateRepositoryError("Provider-state outcome write conflicted")


async def initialize_provider_state_indexes(
    collection: AsyncCollection[MongoDocument],
) -> None:
    """Create only the persistent Provider/Model identity index; no TTL index."""
    try:
        await collection.create_index(
            [("provider_name", 1), ("model_name", 1)],
            name="uq_provider_state_identity_v1",
            unique=True,
        )
    except PyMongoError:
        raise ProviderStateRepositoryError(
            "Provider-state index initialization failed"
        ) from None


__all__ = (
    "MongoProviderStateRepository",
    "initialize_provider_state_indexes",
    "provider_health_from_document",
)
