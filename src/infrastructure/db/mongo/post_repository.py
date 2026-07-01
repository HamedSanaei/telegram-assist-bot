"""MongoDB implementation of :class:`PostRepository` using Motor.

Post documents carry an ``expires_at`` field with a TTL index so
MongoDB removes them automatically after the retention window.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from src.domain.entities import MediaItem, Post, VpnConfig
from src.domain.enums import MediaKind, PostCategory, VpnProtocol, VpnTestStatus
from src.shared.errors import RepositoryError

_COLLECTION = "posts"


def _config_to_doc(config: VpnConfig) -> dict[str, Any]:
    """Serialize a :class:`VpnConfig` into a MongoDB sub-document."""
    return {
        "protocol": config.protocol.value,
        "raw": config.raw,
        "host": config.host,
        "port": config.port,
        "user_id": config.user_id,
        "transport": config.transport,
        "security": config.security,
        "remark": config.remark,
        "extra": config.extra,
        "test_status": config.test_status.value,
    }


def _doc_to_config(doc: dict[str, Any]) -> VpnConfig:
    """Deserialize a MongoDB sub-document into a :class:`VpnConfig`."""
    return VpnConfig(
        protocol=VpnProtocol(doc["protocol"]),
        raw=doc["raw"],
        host=doc["host"],
        port=doc["port"],
        user_id=doc["user_id"],
        transport=doc.get("transport"),
        security=doc.get("security"),
        remark=doc.get("remark"),
        extra=dict(doc.get("extra", {})),
        test_status=VpnTestStatus(doc.get("test_status", "pending")),
    )


class MongoPostRepository:
    """
    Stores collected posts as MongoDB documents keyed by ``post_id``.

    Example:
        repo = MongoPostRepository(motor_client["telegram_admin_bot"])
        await repo.ensure_indexes()
        await repo.save(post)
    """

    def __init__(self, database: AsyncIOMotorDatabase) -> None:
        """
        Args:
            database: Motor database handle.
        """
        self._collection = database[_COLLECTION]

    async def ensure_indexes(self) -> None:
        """
        Create the TTL and lookup indexes. Safe to call on every startup.

        Side effects:
            Creates a TTL index on ``expires_at`` (expire at the stored
            date) and a lookup index on ``content_hash``.
        """
        await self._collection.create_index("expires_at", expireAfterSeconds=0)
        await self._collection.create_index("content_hash")
        await self._collection.create_index([("collected_at", -1)])

    async def save(self, post: Post) -> None:
        """Insert or replace the post document."""
        doc = {
            "_id": post.post_id,
            "source_chat_id": post.source_chat_id,
            "source_message_id": post.source_message_id,
            "text": post.text,
            "content_hash": post.content_hash,
            "media": [
                {
                    "kind": m.kind.value,
                    "file_path": m.file_path,
                    "mime_type": m.mime_type,
                    "file_size": m.file_size,
                }
                for m in post.media
            ],
            "category": post.category.value if post.category else None,
            "ai_provider": post.ai_provider,
            "vpn_configs": [_config_to_doc(c) for c in post.vpn_configs],
            "collected_at": post.collected_at,
            "expires_at": post.expires_at,
        }
        try:
            await self._collection.replace_one({"_id": post.post_id}, doc, upsert=True)
        except Exception as exc:
            raise RepositoryError(f"Mongo save failed: {exc}") from exc

    async def get(self, post_id: str) -> Post | None:
        """Return the post by internal id, or ``None``."""
        doc = await self._collection.find_one({"_id": post_id})
        return self._doc_to_post(doc) if doc else None

    async def find_by_content_hash(self, content_hash: str) -> Post | None:
        """Return one stored post with the same content hash, if any."""
        doc = await self._collection.find_one({"content_hash": content_hash})
        return self._doc_to_post(doc) if doc else None

    async def list_recent_texts(self, limit: int) -> list[str]:
        """Return texts of the most recently collected non-empty posts."""
        cursor = (
            self._collection.find({"text": {"$ne": ""}}, {"text": 1})
            .sort("collected_at", -1)
            .limit(limit)
        )
        return [doc["text"] async for doc in cursor]

    async def update_vpn_configs(self, post_id: str, configs: list[VpnConfig]) -> None:
        """Persist updated VPN config test results."""
        try:
            await self._collection.update_one(
                {"_id": post_id},
                {"$set": {"vpn_configs": [_config_to_doc(c) for c in configs]}},
            )
        except Exception as exc:
            raise RepositoryError(f"Mongo update failed: {exc}") from exc

    async def delete_expired(self, now: datetime) -> int:
        """Delete posts whose ``expires_at`` passed (TTL safety net)."""
        result = await self._collection.delete_many({"expires_at": {"$lte": now}})
        return result.deleted_count

    @staticmethod
    def _doc_to_post(doc: dict[str, Any]) -> Post:
        """Map a MongoDB document to a :class:`Post`."""
        return Post(
            post_id=doc["_id"],
            source_chat_id=doc["source_chat_id"],
            source_message_id=doc["source_message_id"],
            text=doc.get("text", ""),
            content_hash=doc.get("content_hash", ""),
            media=[
                MediaItem(
                    kind=MediaKind(m["kind"]),
                    file_path=m["file_path"],
                    mime_type=m.get("mime_type"),
                    file_size=m.get("file_size"),
                )
                for m in doc.get("media", [])
            ],
            category=PostCategory(doc["category"]) if doc.get("category") else None,
            ai_provider=doc.get("ai_provider"),
            vpn_configs=[_doc_to_config(c) for c in doc.get("vpn_configs", [])],
            collected_at=doc.get("collected_at"),
            expires_at=doc.get("expires_at"),
        )
