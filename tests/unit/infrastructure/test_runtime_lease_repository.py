"""Unit tests for the MongoDB runtime lease adapter."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pymongo.errors import DuplicateKeyError

from src.infrastructure.db.mongo.runtime_lease_repository import (
    MongoRuntimeLeaseRepository,
)


class FakeDatabase:
    """Database fake returning a scripted Mongo collection."""

    def __init__(self, collection: object) -> None:
        """Args: collection: Collection test double returned by item access."""
        self.collection = collection

    def __getitem__(self, name: str) -> object:
        """Return the scripted collection regardless of collection name."""
        del name
        return self.collection


async def test_runtime_lease_acquire_uses_expiry_and_owner_filter() -> None:
    """Lease acquisition must be atomic and allow only expired/current owners."""
    collection = SimpleNamespace(
        find_one_and_update=AsyncMock(return_value={"owner_id": "owner"}),
    )
    repository = MongoRuntimeLeaseRepository(FakeDatabase(collection))  # type: ignore[arg-type]
    now = datetime(2026, 7, 10, 8, 0, tzinfo=timezone.utc)

    acquired = await repository.try_acquire(
        "bot-polling:digest",
        "owner",
        now,
        now + timedelta(seconds=60),
        {"role": "bot-polling"},
    )

    assert acquired is True
    query = collection.find_one_and_update.await_args.args[0]
    assert query["_id"] == "bot-polling:digest"
    assert {"owner_id": "owner"} in query["$or"]
    assert {"expires_at": {"$lte": now}} in query["$or"]


async def test_duplicate_key_means_another_owner_holds_lease() -> None:
    """Mongo upsert contention is a normal refused-acquisition result."""
    collection = SimpleNamespace(
        find_one_and_update=AsyncMock(side_effect=DuplicateKeyError("held")),
    )
    repository = MongoRuntimeLeaseRepository(FakeDatabase(collection))  # type: ignore[arg-type]
    now = datetime.now(timezone.utc)

    assert (
        await repository.try_acquire(
            "collector:digest",
            "second-owner",
            now,
            now + timedelta(seconds=60),
            {},
        )
        is False
    )


async def test_renew_and_release_are_owner_scoped() -> None:
    """Heartbeat and release must never mutate another process's lease."""
    collection = SimpleNamespace(
        update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
        delete_one=AsyncMock(),
    )
    repository = MongoRuntimeLeaseRepository(FakeDatabase(collection))  # type: ignore[arg-type]
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=60)

    assert await repository.renew("collector:digest", "owner", expires_at) is True
    await repository.release("collector:digest", "owner")

    assert collection.update_one.await_args.args[0] == {
        "_id": "collector:digest",
        "owner_id": "owner",
    }
    collection.delete_one.assert_awaited_once_with(
        {"_id": "collector:digest", "owner_id": "owner"}
    )
