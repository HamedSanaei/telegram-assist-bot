"""Mongo-free proofs for the bounded approval outbox claim hot path."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest

from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoOperationalApprovalRepository,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class PoisonedPreparations:
    """Fail loudly when any polling path touches readiness documents."""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"The polling path touched content_preparations.{name}")


class RecordingCollection:
    """Record every operation while serving a tiny in-memory document store."""

    def __init__(self, documents: list[dict[str, Any]] | None = None) -> None:
        self.documents = {str(item["_id"]): dict(item) for item in documents or []}
        self.calls: list[str] = []

    def find(
        self,
        query: dict[str, Any] | None = None,
        projection: dict[str, Any] | None = None,
    ) -> RecordingCursor:
        self.calls.append("find")
        return RecordingCursor(self._matching(query or {}), projection=projection)

    async def find_one(
        self,
        query: dict[str, Any],
        projection: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        self.calls.append("find_one")
        matching = self._matching(query)
        if not matching:
            return None
        return _project(matching[0], projection)

    async def update_one(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> SimpleUpdateResult:
        self.calls.append("update_one")
        matching = self._matching(query)
        if not matching:
            if not upsert:
                return SimpleUpdateResult(0, None)
            document = {"_id": str(query["_id"])}
            _apply_update(document, update, inserting=True)
            self.documents[str(document["_id"])] = document
            return SimpleUpdateResult(0, str(document["_id"]))
        document = matching[0]
        _apply_update(document, update, inserting=False)
        return SimpleUpdateResult(1, None)

    async def find_one_and_update(
        self,
        query: dict[str, Any],
        update: dict[str, Any],
        *,
        sort: list[tuple[str, int]] | None = None,
        return_document: object = None,
    ) -> dict[str, Any] | None:
        del return_document
        self.calls.append("find_one_and_update")
        matching = self._matching(query)
        if sort is not None:
            field, direction = sort[0]
            matching.sort(
                key=lambda item: (
                    item.get(field) is None,
                    item.get(field),
                    str(item["_id"]),
                ),
                reverse=direction < 0,
            )
        if not matching:
            return None
        document = matching[0]
        _apply_update(document, update, inserting=False)
        return dict(document)

    def _matching(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        if "$or" in query:
            candidates = [
                item
                for item in self.documents.values()
                if any(_matches(item, branch) for branch in query["$or"])
            ]
        elif "_id" in query and type(query["_id"]) is dict and "$in" in query["_id"]:
            identifiers = {str(item) for item in query["_id"]["$in"]}
            candidates = [
                item
                for item in self.documents.values()
                if str(item["_id"]) in identifiers
            ]
        else:
            candidates = [
                item for item in self.documents.values() if _matches(item, query)
            ]
        return candidates


class RecordingCursor:
    """Provide the sorted, limited async cursor the repository consumes."""

    def __init__(
        self,
        documents: list[dict[str, Any]],
        *,
        projection: dict[str, Any] | None = None,
    ) -> None:
        self._documents = documents
        self._projection = projection
        self._limit: int | None = None

    def sort(self, keys: list[tuple[str, int]]) -> RecordingCursor:
        for field, direction in reversed(keys):
            self._documents.sort(
                key=lambda item: (
                    item.get(field) is None,
                    item.get(field),
                    str(item["_id"]),
                ),
                reverse=direction < 0,
            )
        return self

    def limit(self, count: int) -> RecordingCursor:
        self._limit = count
        return self

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        async def iterate() -> AsyncIterator[dict[str, Any]]:
            documents = self._documents
            if self._limit is not None:
                documents = documents[: self._limit]
            for item in documents:
                yield _project(item, self._projection)

        return iterate()


class SimpleUpdateResult:
    """Mirror the updated pymongo result fields the repository reads."""

    def __init__(self, modified_count: int, upserted_id: str | None) -> None:
        self.modified_count = modified_count
        self.upserted_id = upserted_id


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for field, expectation in query.items():
        if field == "$or":
            continue
        value = document.get(field)
        if type(expectation) is dict:
            if "$exists" in expectation:
                if (value is not None) is not expectation["$exists"]:
                    return False
                continue
            if "$gt" in expectation and not (
                value is not None and value > expectation["$gt"]
            ):
                return False
            if "$lte" in expectation and not (
                value is not None and value <= expectation["$lte"]
            ):
                return False
            continue
        if value != expectation:
            return False
    return True


def _apply_update(
    document: dict[str, Any], update: dict[str, Any], *, inserting: bool
) -> None:
    if inserting:
        document.update(update.get("$setOnInsert", {}))
    document.update(update.get("$set", {}))
    for key, value in update.get("$inc", {}).items():
        document[key] = document.get(key, 0) + value


def _project(
    document: dict[str, Any], projection: dict[str, Any] | None
) -> dict[str, Any]:
    if projection is None:
        return dict(document)
    return {
        key: value
        for key, value in document.items()
        if key == "_id" or projection.get(key) == 1
    }


def _repository(
    *,
    preparations: object,
    deliveries: RecordingCollection,
    state: RecordingCollection | None = None,
) -> MongoOperationalApprovalRepository:
    return MongoOperationalApprovalRepository(
        cast("Any", preparations),
        cast("Any", deliveries),
        outbox_state=cast("Any", state),
    )


def test_claim_ready_never_touches_content_preparations() -> None:
    async def scenario() -> None:
        deliveries = RecordingCollection()
        repository = _repository(
            preparations=PoisonedPreparations(), deliveries=deliveries
        )
        claim = await repository.claim_ready(
            owner="worker", now=_NOW, lease_until=_NOW + timedelta(seconds=30)
        )
        assert claim is None
        assert deliveries.calls == ["find_one_and_update"]

    asyncio.run(scenario())


def test_claim_ready_is_one_bounded_operation_for_large_history() -> None:
    async def scenario() -> None:
        deliveries = RecordingCollection(
            [
                {
                    "_id": f"completed-{index}",
                    "status": "completed",
                    "ready_at": _NOW,
                    "claim_due_at": None,
                }
                for index in range(5000)
            ]
            + [
                {
                    "_id": "ready",
                    "status": "pending",
                    "ready_at": _NOW,
                    "claim_due_at": _NOW,
                }
            ]
        )
        repository = _repository(
            preparations=PoisonedPreparations(), deliveries=deliveries
        )
        claim = await repository.claim_ready(
            owner="worker", now=_NOW, lease_until=_NOW + timedelta(seconds=30)
        )
        assert claim is not None
        assert claim.post_id == "ready"
        assert deliveries.calls == ["find_one_and_update"]

    asyncio.run(scenario())


def test_ensure_delivery_creates_one_identity_and_is_idempotent() -> None:
    async def scenario() -> None:
        deliveries = RecordingCollection()
        repository = _repository(
            preparations=PoisonedPreparations(), deliveries=deliveries
        )
        assert await repository.ensure_delivery("post-1", ready_at=_NOW)
        assert not await repository.ensure_delivery("post-1", ready_at=_NOW)
        assert set(deliveries.documents) == {"post-1"}
        assert deliveries.documents["post-1"] == {
            "_id": "post-1",
            "status": "pending",
            "ready_at": _NOW,
            "created_at": _NOW,
            "claim_due_at": _NOW,
            "attempt_count": 0,
            "administrator_deliveries": {},
            "destination_statuses": {},
            "sync_version": 0,
            "sync_required": False,
        }

    asyncio.run(scenario())


def test_ensure_delivery_preserves_existing_progress_and_status() -> None:
    async def scenario() -> None:
        existing = {
            "_id": "post-1",
            "status": "completed",
            "ready_at": _NOW,
            "created_at": _NOW,
            "claim_due_at": None,
            "attempt_count": 4,
            "administrator_deliveries": {"7": {"status": "completed"}},
            "destination_statuses": {"-1001": {"status": "published"}},
            "sync_version": 3,
            "sync_required": False,
        }
        deliveries = RecordingCollection([existing])
        repository = _repository(
            preparations=PoisonedPreparations(), deliveries=deliveries
        )
        assert not await repository.ensure_delivery("post-1", ready_at=_NOW)
        assert deliveries.documents["post-1"] == existing

    asyncio.run(scenario())


def test_ensure_delivery_repairs_only_missing_order_fields() -> None:
    async def scenario() -> None:
        deliveries = RecordingCollection(
            [{"_id": "legacy", "status": "pending", "attempt_count": 2}]
        )
        repository = _repository(
            preparations=PoisonedPreparations(), deliveries=deliveries
        )
        assert not await repository.ensure_delivery("legacy", ready_at=_NOW)
        assert deliveries.documents["legacy"] == {
            "_id": "legacy",
            "status": "pending",
            "attempt_count": 2,
            "claim_due_at": _NOW,
            "created_at": _NOW,
        }

    asyncio.run(scenario())


def test_reconcile_backfill_is_bounded_incremental_and_idempotent() -> None:
    async def scenario() -> None:
        preparations = RecordingCollection(
            [
                {
                    "_id": f"post-{index:04d}",
                    "ready_at": _NOW + timedelta(seconds=index),
                }
                for index in range(250)
            ]
            + [{"_id": "unready"} for _ in range(50)]
        )
        deliveries = RecordingCollection()
        state = RecordingCollection()
        repository = _repository(
            preparations=preparations, deliveries=deliveries, state=state
        )

        first = await repository.reconcile_missing_deliveries(limit=100, at=_NOW)
        assert (first.scanned_count, first.created_count, first.existing_count) == (
            100,
            100,
            0,
        )
        assert first.completed is False
        assert first.watermark == _NOW + timedelta(seconds=99)

        second = await repository.reconcile_missing_deliveries(limit=100, at=_NOW)
        assert (second.scanned_count, second.created_count) == (100, 100)
        assert second.completed is False

        third = await repository.reconcile_missing_deliveries(limit=100, at=_NOW)
        assert (third.scanned_count, third.created_count) == (50, 50)
        assert third.completed is True

        fourth = await repository.reconcile_missing_deliveries(limit=100, at=_NOW)
        assert (fourth.scanned_count, fourth.created_count) == (0, 0)
        assert fourth.completed is True

        assert len(deliveries.documents) == 250
        assert "unready" not in deliveries.documents
        assert (
            state.documents["content_preparation_outbox"]["watermark_id"] == "post-0249"
        )

    asyncio.run(scenario())


def test_reconcile_does_not_rewrite_existing_identities() -> None:
    async def scenario() -> None:
        preparations = RecordingCollection(
            [
                {"_id": f"post-{index}", "ready_at": _NOW + timedelta(seconds=index)}
                for index in range(3)
            ]
        )
        deliveries = RecordingCollection()
        state = RecordingCollection()
        repository = _repository(
            preparations=preparations, deliveries=deliveries, state=state
        )
        for index in range(3):
            await repository.ensure_delivery(
                f"post-{index}", ready_at=_NOW + timedelta(seconds=index)
            )
        deliveries.calls.clear()

        result = await repository.reconcile_missing_deliveries(limit=10, at=_NOW)

        assert (result.scanned_count, result.created_count, result.existing_count) == (
            3,
            0,
            3,
        )
        assert result.completed is True
        assert deliveries.calls == ["find"]

    asyncio.run(scenario())


def test_reconcile_rejects_invalid_configuration() -> None:
    async def scenario() -> None:
        repository = _repository(
            preparations=RecordingCollection(),
            deliveries=RecordingCollection(),
        )
        with pytest.raises(ValueError, match="state collection"):
            await repository.reconcile_missing_deliveries(limit=10, at=_NOW)
        with_state = _repository(
            preparations=RecordingCollection(),
            deliveries=RecordingCollection(),
            state=RecordingCollection(),
        )
        with pytest.raises(ValueError, match="limit"):
            await with_state.reconcile_missing_deliveries(limit=0, at=_NOW)
        with pytest.raises(ValueError, match="limit"):
            await with_state.reconcile_missing_deliveries(limit=5000, at=_NOW)

    asyncio.run(scenario())
