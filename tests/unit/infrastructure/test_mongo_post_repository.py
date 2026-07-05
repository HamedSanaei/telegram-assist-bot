"""Unit tests for the MongoDB post repository adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.domain.entities import Post, TextEntity
from src.domain.enums import PostCategory
from src.infrastructure.db.mongo.post_repository import MongoPostRepository


class FakeReplaceResult:
    """Placeholder result for Mongo replace operations."""


class FakeCollection:
    """Small async collection fake for repository contract tests."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}
        self.indexes: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def create_index(self, *args: Any, **kwargs: Any) -> None:
        """Record requested indexes."""
        self.indexes.append((args, kwargs))

    async def replace_one(
        self, query: dict[str, Any], doc: dict[str, Any], upsert: bool = False
    ) -> FakeReplaceResult:
        """Store the replacement document by ``_id``."""
        self.docs[str(doc["_id"])] = dict(doc)
        return FakeReplaceResult()

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        """Return the first document matching simple equality predicates."""
        for doc in self.docs.values():
            if all(doc.get(key) == value for key, value in query.items()):
                return dict(doc)
        return None


class FakeDatabase:
    """Database fake returning one collection by name."""

    def __init__(self) -> None:
        self.collection = FakeCollection()

    def __getitem__(self, name: str) -> FakeCollection:
        """Return the fake collection."""
        return self.collection


class TestMongoPostRepository:
    """Tests for :class:`MongoPostRepository`."""

    async def test_ensure_indexes_includes_ttl_and_source_identity(self) -> None:
        database = FakeDatabase()
        repo = MongoPostRepository(database)  # type: ignore[arg-type]

        await repo.ensure_indexes()

        assert (("expires_at",), {"expireAfterSeconds": 0}) in database.collection.indexes
        assert any(
            kwargs.get("unique") is True and kwargs.get("name") == "uniq_source_message"
            for _, kwargs in database.collection.indexes
        )

    async def test_source_identity_and_skip_fields_roundtrip(self) -> None:
        database = FakeDatabase()
        repo = MongoPostRepository(database)  # type: ignore[arg-type]
        post = Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=55,
            grouped_id=777,
            text="خبر",
            content_hash="hash",
            category=PostCategory.GENERAL_NEWS,
            is_duplicate=True,
            duplicate_of="p0",
            duplicate_provider="fake",
            skipped_reason="duplicate",
            collected_at=datetime.now(timezone.utc),
            expires_at=datetime.now(timezone.utc),
        )

        await repo.save(post)
        loaded = await repo.find_by_source_message(-100, 55, 777)

        assert loaded is not None
        assert loaded.grouped_id == 777
        assert loaded.is_duplicate is True
        assert loaded.duplicate_of == "p0"
        assert loaded.duplicate_provider == "fake"
        assert loaded.skipped_reason == "duplicate"

    async def test_text_entities_roundtrip(self) -> None:
        """Stored custom emoji entities are preserved through Mongo mapping."""
        database = FakeDatabase()
        repo = MongoPostRepository(database)  # type: ignore[arg-type]
        post = Post(
            post_id="p1",
            source_chat_id=-100,
            source_message_id=55,
            text="premium *",
            content_hash="hash",
            text_entities=[
                TextEntity(
                    kind="custom_emoji",
                    offset=8,
                    length=1,
                    data={"document_id": 123456789},
                )
            ],
        )

        await repo.save(post)
        loaded = await repo.get("p1")

        assert loaded is not None
        assert loaded.text_entities == post.text_entities
