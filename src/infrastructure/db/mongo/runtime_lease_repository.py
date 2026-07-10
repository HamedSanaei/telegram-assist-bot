"""MongoDB persistence adapter for distributed runtime leases."""

from __future__ import annotations

from datetime import datetime

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from src.shared.errors import RepositoryError

_COLLECTION = "runtime_leases"


class MongoRuntimeLeaseRepository:
    """Atomically coordinates runtime component ownership through MongoDB."""

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        """Args: database: Motor database containing runtime lease state."""
        self._collection = database[_COLLECTION]

    async def ensure_indexes(self) -> None:
        """Create the TTL index that removes expired lease documents."""
        try:
            await self._collection.create_index(
                "expires_at",
                expireAfterSeconds=0,
                name="ttl_runtime_lease_expiry",
            )
        except Exception as exc:
            raise RepositoryError(f"Cannot create runtime lease indexes: {exc}") from exc

    async def try_acquire(
        self,
        lease_id: str,
        owner_id: str,
        now: datetime,
        expires_at: datetime,
        metadata: dict[str, object],
    ) -> bool:
        """Atomically acquire an absent, expired, or already-owned lease."""
        try:
            document = await self._collection.find_one_and_update(
                {
                    "_id": lease_id,
                    "$or": [
                        {"owner_id": owner_id},
                        {"expires_at": {"$lte": now}},
                    ],
                },
                {
                    "$set": {
                        "owner_id": owner_id,
                        "expires_at": expires_at,
                        "updated_at": now,
                        "metadata": dict(metadata),
                    },
                    "$setOnInsert": {"created_at": now},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        except Exception as exc:
            raise RepositoryError(f"Cannot acquire runtime lease: {exc}") from exc
        return document is not None and document.get("owner_id") == owner_id

    async def renew(
        self, lease_id: str, owner_id: str, expires_at: datetime
    ) -> bool:
        """Extend a lease only while this process still owns it."""
        try:
            result = await self._collection.update_one(
                {"_id": lease_id, "owner_id": owner_id},
                {
                    "$set": {
                        "expires_at": expires_at,
                        "updated_at": datetime.now(expires_at.tzinfo),
                    }
                },
            )
        except Exception as exc:
            raise RepositoryError(f"Cannot renew runtime lease: {exc}") from exc
        return result.matched_count == 1

    async def release(self, lease_id: str, owner_id: str) -> None:
        """Delete a lease only if this process still owns it."""
        try:
            await self._collection.delete_one(
                {"_id": lease_id, "owner_id": owner_id}
            )
        except Exception as exc:
            raise RepositoryError(f"Cannot release runtime lease: {exc}") from exc
