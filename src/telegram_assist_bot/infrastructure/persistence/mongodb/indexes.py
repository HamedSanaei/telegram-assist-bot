"""Initialize and verify the exact MongoDB indexes required for posts."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast

from pymongo import ASCENDING, IndexModel
from pymongo.errors import OperationFailure, PyMongoError

from telegram_assist_bot.infrastructure.persistence.mongodb.errors import (
    MongoIndexInitializationError,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

POST_SOURCE_IDENTITY_INDEX_NAME: Final[str] = "uq_posts_source_identity_v1"
"""Stable name of the source-channel and source-message unique index."""

POST_EXPIRATION_INDEX_NAME: Final[str] = "ttl_posts_expires_at_v1"
"""Stable name of the absolute post-expiration TTL index."""

POST_SEMANTIC_WINDOW_INDEX_NAME: Final[str] = "ix_posts_semantic_window_v1"
"""Support status-scoped newest-first semantic candidate scans."""

_NAMESPACE_NOT_FOUND_CODE: Final[int] = 26
_INDEX_OPTION_FIELDS: Final[tuple[str, ...]] = (
    "collation",
    "expireAfterSeconds",
    "partialFilterExpression",
    "sparse",
    "unique",
)


@dataclass(frozen=True, slots=True)
class PostIndexSpec:
    """Describe one exact, restart-stable MongoDB post index."""

    name: str
    keys: tuple[tuple[str, int], ...]
    unique: bool = False
    expire_after_seconds: int | None = None

    def as_index_model(self) -> IndexModel:
        """Convert the owned specification to a driver index model."""
        options: dict[str, object] = {"name": self.name}
        if self.unique:
            options["unique"] = True
        if self.expire_after_seconds is not None:
            options["expireAfterSeconds"] = self.expire_after_seconds
        return IndexModel(list(self.keys), **options)


POST_INDEX_SPECS: Final[tuple[PostIndexSpec, ...]] = (
    PostIndexSpec(
        name=POST_SOURCE_IDENTITY_INDEX_NAME,
        keys=(
            ("source_channel_id", ASCENDING),
            ("source_message_id", ASCENDING),
        ),
        unique=True,
    ),
    PostIndexSpec(
        name=POST_EXPIRATION_INDEX_NAME,
        keys=(("expires_at", ASCENDING),),
        expire_after_seconds=0,
    ),
    PostIndexSpec(
        name=POST_SEMANTIC_WINDOW_INDEX_NAME,
        keys=(("status", ASCENDING), ("received_at", -1), ("_id", ASCENDING)),
    ),
)
"""The complete index set owned by T004, excluding MongoDB's `_id_` index."""


def _document_keys(
    document: Mapping[str, object],
) -> tuple[tuple[str, int], ...] | None:
    raw_keys = document.get("key")
    if not isinstance(raw_keys, Mapping):
        return None
    keys: list[tuple[str, int]] = []
    for field_name, direction in raw_keys.items():
        if (
            type(field_name) is not str
            or not isinstance(direction, int)
            or isinstance(direction, bool)
        ):
            return None
        keys.append((field_name, int(direction)))
    return tuple(keys)


def _matches_spec(document: Mapping[str, object], spec: PostIndexSpec) -> bool:
    if document.get("name") != spec.name or _document_keys(document) != spec.keys:
        return False
    expected_options: dict[str, object] = {
        "unique": spec.unique,
        "expireAfterSeconds": spec.expire_after_seconds,
    }
    actual_options: dict[str, object] = {
        "unique": document.get("unique", False),
        "expireAfterSeconds": document.get("expireAfterSeconds"),
    }
    if actual_options != expected_options:
        return False
    return all(
        option not in document
        for option in _INDEX_OPTION_FIELDS
        if option not in {"unique", "expireAfterSeconds"}
    )


def _validate_existing_indexes(
    documents: tuple[Mapping[str, object], ...],
) -> frozenset[str]:
    matched: set[str] = set()
    for document in documents:
        name = document.get("name")
        if name == "_id_":
            continue
        keys = _document_keys(document)
        for spec in POST_INDEX_SPECS:
            if name != spec.name and keys != spec.keys:
                continue
            if not _matches_spec(document, spec):
                raise MongoIndexInitializationError
            matched.add(spec.name)
    return frozenset(matched)


async def _list_indexes(
    collection: AsyncCollection[MongoDocument],
) -> tuple[Mapping[str, object], ...]:
    try:
        cursor = await collection.list_indexes()
        documents = await cursor.to_list()
    except OperationFailure as error:
        if error.code == _NAMESPACE_NOT_FOUND_CODE:
            return ()
        raise
    return tuple(cast("Mapping[str, object]", document) for document in documents)


async def initialize_post_indexes(
    collection: AsyncCollection[MongoDocument],
    *,
    timeout_seconds: int,
) -> None:
    """Create missing indexes and fail safely on any incompatible definition."""
    failed = False
    try:
        async with asyncio.timeout(timeout_seconds):
            before = await _list_indexes(collection)
            matched = _validate_existing_indexes(before)
            missing = [
                spec.as_index_model()
                for spec in POST_INDEX_SPECS
                if spec.name not in matched
            ]
            if missing:
                await collection.create_indexes(missing)
            after = await _list_indexes(collection)
            final_matches = _validate_existing_indexes(after)
            if final_matches != frozenset(spec.name for spec in POST_INDEX_SPECS):
                raise MongoIndexInitializationError
    except MongoIndexInitializationError:
        raise
    except (PyMongoError, TimeoutError, TypeError, ValueError):
        failed = True

    if failed:
        raise MongoIndexInitializationError


__all__ = (
    "POST_EXPIRATION_INDEX_NAME",
    "POST_INDEX_SPECS",
    "POST_SEMANTIC_WINDOW_INDEX_NAME",
    "POST_SOURCE_IDENTITY_INDEX_NAME",
    "PostIndexSpec",
    "initialize_post_indexes",
)
