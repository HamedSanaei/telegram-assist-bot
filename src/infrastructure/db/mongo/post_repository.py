"""MongoDB implementation of :class:`PostRepository` using Motor.

Post documents carry an ``expires_at`` field with a TTL index so
MongoDB removes them automatically after the retention window.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from src.domain.entities import (
    MediaItem,
    Post,
    PostQualityScore,
    PostSourceMetrics,
    TextEntity,
    VpnConfig,
)
from src.domain.enums import (
    IngestionMode,
    MediaDownloadStatus,
    MediaKind,
    PostCategory,
    QualityScoreStatus,
    SourceMetricsStatus,
    VpnProtocol,
    VpnTestStatus,
)
from src.shared.errors import RepositoryError

_COLLECTION = "posts"


def _as_utc(value: datetime | None) -> datetime | None:
    """
    Return a timezone-aware UTC datetime from MongoDB values.

    Motor/PyMongo may deserialize datetimes as offset-naive UTC values. The
    domain treats timestamps as UTC-aware, so normalize them at the repository
    boundary.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def _metrics_to_doc(metrics: PostSourceMetrics) -> dict[str, Any]:
    """Serialize source metrics into a MongoDB sub-document."""
    return {
        "views": metrics.views,
        "forwards": metrics.forwards,
        "replies_count": metrics.replies_count,
        "reactions_count": metrics.reactions_count,
        "source_published_at": metrics.source_published_at,
    }


def _doc_to_metrics(doc: dict[str, Any] | None) -> PostSourceMetrics:
    """Deserialize source metrics from a MongoDB sub-document."""
    doc = doc or {}
    return PostSourceMetrics(
        views=doc.get("views"),
        forwards=doc.get("forwards"),
        replies_count=doc.get("replies_count"),
        reactions_count=doc.get("reactions_count"),
        source_published_at=_as_utc(doc.get("source_published_at")),
    )


def _quality_score_to_doc(score: PostQualityScore | None) -> dict[str, Any] | None:
    """Serialize quality score into a MongoDB sub-document."""
    if score is None:
        return None
    return {
        "score": score.score,
        "reason": score.reason,
        "provider": score.provider,
        "scored_at": score.scored_at,
        "metrics": score.metrics,
    }


def _doc_to_quality_score(doc: dict[str, Any] | None) -> PostQualityScore | None:
    """Deserialize quality score from a MongoDB sub-document."""
    if not doc:
        return None
    return PostQualityScore(
        score=float(doc.get("score", 0)),
        reason=str(doc.get("reason", "")),
        provider=str(doc.get("provider", "")),
        scored_at=_as_utc(doc.get("scored_at")),
        metrics=dict(doc.get("metrics", {})),
    )


def _entity_to_doc(entity: TextEntity) -> dict[str, Any]:
    """Serialize a framework-neutral text entity into MongoDB."""
    return {
        "kind": entity.kind,
        "offset": entity.offset,
        "length": entity.length,
        "data": dict(entity.data),
    }


def _doc_to_entity(doc: dict[str, Any]) -> TextEntity:
    """Deserialize a MongoDB text-entity sub-document."""
    return TextEntity(
        kind=str(doc.get("kind", "")),
        offset=int(doc.get("offset", 0)),
        length=int(doc.get("length", 0)),
        data=dict(doc.get("data", {})),
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
            date), unique source identity index, and lookup indexes for
            content hash and recent-post scans.
        """
        await self._collection.create_index("expires_at", expireAfterSeconds=0)
        await self._collection.create_index("content_hash")
        await self._collection.create_index("vpn_fingerprints")
        await self._collection.create_index([("collected_at", -1)])
        await self._collection.create_index(
            [
                ("source_chat_id", 1),
                ("source_message_id", 1),
                ("grouped_id", 1),
            ],
            unique=True,
            name="uniq_source_message",
        )

    async def save(self, post: Post) -> None:
        """Insert or replace the post document."""
        doc = self._post_to_doc(post)
        try:
            await self._collection.replace_one({"_id": post.post_id}, doc, upsert=True)
        except Exception as exc:
            raise RepositoryError(f"Mongo save failed: {exc}") from exc

    async def insert_if_absent(self, post: Post) -> bool:
        """Atomically insert a new post, treating source conflicts as benign."""
        try:
            await self._collection.insert_one(self._post_to_doc(post))
            return True
        except DuplicateKeyError:
            return False
        except Exception as exc:
            raise RepositoryError(f"Mongo insert failed: {exc}") from exc

    @staticmethod
    def _post_to_doc(post: Post) -> dict[str, Any]:
        """Serialize one post into its MongoDB document representation."""
        return {
            "_id": post.post_id,
            "source_chat_id": post.source_chat_id,
            "source_message_id": post.source_message_id,
            "source_label": post.source_label,
            "grouped_id": post.grouped_id,
            "text": post.text,
            "text_entities": [_entity_to_doc(entity) for entity in post.text_entities],
            "content_hash": post.content_hash,
            "ingestion_mode": post.ingestion_mode.value,
            "quality_score_status": post.quality_score_status.value,
            "source_metrics_status": post.source_metrics_status.value,
            "vpn_fingerprints": list(post.vpn_fingerprints),
            "media": [
                {
                    "kind": m.kind.value,
                    "file_path": m.file_path,
                    "mime_type": m.mime_type,
                    "file_size": m.file_size,
                }
                for m in post.media
            ],
            "expected_media_count": post.expected_media_count,
            "media_download_status": post.media_download_status.value,
            "category": post.category.value if post.category else None,
            "ai_provider": post.ai_provider,
            "is_duplicate": post.is_duplicate,
            "duplicate_of": post.duplicate_of,
            "duplicate_provider": post.duplicate_provider,
            "skipped_reason": post.skipped_reason,
            "source_metrics": _metrics_to_doc(post.source_metrics),
            "quality_score": _quality_score_to_doc(post.quality_score),
            "vpn_configs": [_config_to_doc(c) for c in post.vpn_configs],
            "collected_at": post.collected_at,
            "expires_at": post.expires_at,
        }

    async def get(self, post_id: str) -> Post | None:
        """Return the post by internal id, or ``None``."""
        doc = await self._collection.find_one({"_id": post_id})
        return self._doc_to_post(doc) if doc else None

    async def find_by_content_hash(self, content_hash: str) -> Post | None:
        """Return one stored post with the same content hash, if any."""
        primary_query = {
            "content_hash": content_hash,
            "is_duplicate": {"$ne": True},
            "$or": [
                {"skipped_reason": None},
                {"skipped_reason": {"$exists": False}},
            ],
        }
        doc = await self._collection.find_one(primary_query)
        if doc is None:
            doc = await self._collection.find_one({"content_hash": content_hash})
        return self._doc_to_post(doc) if doc else None

    async def find_by_source_message(
        self, source_chat_id: int, source_message_id: int, grouped_id: int | None = None
    ) -> Post | None:
        """Return one stored post by source Telegram identity, if any."""
        doc = await self._collection.find_one(
            {
                "source_chat_id": source_chat_id,
                "source_message_id": source_message_id,
                "grouped_id": grouped_id,
            }
        )
        return self._doc_to_post(doc) if doc else None

    async def list_recent_texts(self, limit: int) -> list[str]:
        """Return texts of recent non-skipped, non-duplicate posts."""
        cursor = (
            self._collection.find(
                {
                    "text": {"$ne": ""},
                    "is_duplicate": {"$ne": True},
                    "$or": [
                        {"skipped_reason": None},
                        {"skipped_reason": {"$exists": False}},
                    ],
                },
                {"text": 1},
            )
            .sort("collected_at", -1)
            .limit(limit)
        )
        return [doc["text"] async for doc in cursor]

    async def find_seen_vpn_fingerprints(self, fingerprints: list[str]) -> set[str]:
        """Return VPN URI fingerprints already stored in any post document."""
        if not fingerprints:
            return set()
        cursor = self._collection.find(
            {"vpn_fingerprints": {"$in": fingerprints}},
            {"vpn_fingerprints": 1},
        )
        requested = set(fingerprints)
        seen: set[str] = set()
        async for doc in cursor:
            seen.update(requested.intersection(doc.get("vpn_fingerprints", [])))
        return seen

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
            source_label=str(doc.get("source_label", "")),
            text=doc.get("text", ""),
            content_hash=doc.get("content_hash", ""),
            ingestion_mode=IngestionMode(
                doc.get("ingestion_mode", IngestionMode.CONFIGURED_SOURCE.value)
            ),
            quality_score_status=QualityScoreStatus(
                doc.get(
                    "quality_score_status",
                    (
                        QualityScoreStatus.SCORED.value
                        if doc.get("quality_score")
                        else QualityScoreStatus.PENDING.value
                    ),
                )
            ),
            source_metrics_status=SourceMetricsStatus(
                doc.get(
                    "source_metrics_status",
                    (
                        SourceMetricsStatus.NOT_REQUIRED.value
                        if doc.get("ingestion_mode")
                        == IngestionMode.DIALOG_VPN_DISCOVERY.value
                        else SourceMetricsStatus.PENDING.value
                    ),
                )
            ),
            vpn_fingerprints=list(doc.get("vpn_fingerprints", [])),
            text_entities=[
                _doc_to_entity(entity) for entity in doc.get("text_entities", [])
            ],
            grouped_id=doc.get("grouped_id"),
            media=[
                MediaItem(
                    kind=MediaKind(m["kind"]),
                    file_path=m["file_path"],
                    mime_type=m.get("mime_type"),
                    file_size=m.get("file_size"),
                )
                for m in doc.get("media", [])
            ],
            expected_media_count=int(doc.get("expected_media_count", 0)),
            media_download_status=MediaDownloadStatus(
                doc.get("media_download_status", MediaDownloadStatus.COMPLETE.value)
            ),
            category=PostCategory(doc["category"]) if doc.get("category") else None,
            ai_provider=doc.get("ai_provider"),
            is_duplicate=bool(doc.get("is_duplicate", False)),
            duplicate_of=doc.get("duplicate_of"),
            duplicate_provider=doc.get("duplicate_provider"),
            skipped_reason=doc.get("skipped_reason"),
            source_metrics=_doc_to_metrics(doc.get("source_metrics")),
            quality_score=_doc_to_quality_score(doc.get("quality_score")),
            vpn_configs=[_doc_to_config(c) for c in doc.get("vpn_configs", [])],
            collected_at=_as_utc(doc.get("collected_at")),
            expires_at=_as_utc(doc.get("expires_at")),
        )
