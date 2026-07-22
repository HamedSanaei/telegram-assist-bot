"""MongoDB first-valid-write-wins AI result cache adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

from pydantic import ValidationError
from pymongo import ASCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ai.contracts import AIResult
from telegram_assist_bot.application.ai.response_validator import ResponseValidator
from telegram_assist_bot.application.ports.ai_cache_repository import (
    AICacheEntry,
    AICacheRepository,
    AICacheRepositoryError,
    AICacheWriteResult,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.application.ai.cache_key import AICacheIdentity
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

AI_CACHE_SCHEMA_VERSION: Final[int] = 1


def _entry_to_document(entry: AICacheEntry) -> dict[str, Any]:
    return {
        "_id": entry.identity.cache_key,
        "cache_key": entry.identity.cache_key,
        "task_type": entry.identity.task_type.value,
        "input_hash": entry.identity.input_hash,
        "prompt_version": entry.identity.prompt_version,
        "schema_version": entry.identity.schema_version,
        "language": entry.identity.language,
        "key_version": entry.identity.key_version,
        "result": entry.result.model_dump(mode="json"),
        "created_at": entry.created_at.astimezone(UTC),
        "expires_at": entry.expires_at.astimezone(UTC),
        "cache_schema_version": entry.cache_schema_version,
    }


def _entry_from_document(
    document: dict[str, Any], identity: AICacheIdentity, as_of: datetime
) -> AICacheEntry | None:
    if (
        document.get("cache_schema_version") != AI_CACHE_SCHEMA_VERSION
        or document.get("cache_key") != identity.cache_key
        or document.get("task_type") != identity.task_type.value
        or document.get("input_hash") != identity.input_hash
        or document.get("prompt_version") != identity.prompt_version
        or document.get("schema_version") != identity.schema_version
        or document.get("language") != identity.language
        or document.get("key_version") != identity.key_version
    ):
        return None
    raw_created_at = document.get("created_at")
    raw_expires_at = document.get("expires_at")
    if not isinstance(raw_created_at, datetime) or not isinstance(
        raw_expires_at, datetime
    ):
        return None
    created_at = raw_created_at.replace(tzinfo=UTC)
    expires_at = raw_expires_at.replace(tzinfo=UTC)
    if expires_at <= as_of.astimezone(UTC) or created_at > expires_at:
        return None
    try:
        result = AIResult.model_validate(document.get("result"))
    except ValidationError:
        return None
    if (
        not result.success
        or result.task_type is not identity.task_type
        or result.prompt_version != identity.prompt_version
        or result.schema_version != identity.schema_version
        or result.result is None
    ):
        return None
    try:
        ResponseValidator().validate(
            result.result,
            identity.task_type,
            identity.schema_version,
        )
    except Exception:  # noqa: BLE001
        return None
    return AICacheEntry(
        identity=identity,
        result=result,
        created_at=created_at,
        expires_at=expires_at,
        cache_schema_version=AI_CACHE_SCHEMA_VERSION,
    )


class MongoAICacheRepository(AICacheRepository):
    """Store disposable validated results with explicit expiry checks."""

    def __init__(self, collection: AsyncCollection[MongoDocument]) -> None:
        """Initialize the isolated cache collection adapter."""
        self._collection = collection

    async def get(
        self, identity: AICacheIdentity, *, as_of: datetime
    ) -> AICacheEntry | None:
        """Return a compatible entry only when it is explicitly unexpired."""
        try:
            document = await self._collection.find_one({"_id": identity.cache_key})
        except PyMongoError:
            raise AICacheRepositoryError("ai_cache_read_failed") from None
        if document is None:
            return None
        return _entry_from_document(document, identity, as_of)

    async def put_if_absent(self, entry: AICacheEntry) -> AICacheWriteResult:
        """Insert once and accept the first valid concurrent writer's result."""
        document = _entry_to_document(entry)
        validated = _entry_from_document(document, entry.identity, entry.created_at)
        if validated is None:
            raise AICacheRepositoryError("ai_cache_invalid_result")
        try:
            await self._collection.insert_one(document)
            return AICacheWriteResult(entry=entry, created=True)
        except DuplicateKeyError:
            try:
                stored = await self._collection.find_one(
                    {"_id": entry.identity.cache_key}
                )
            except PyMongoError:
                raise AICacheRepositoryError(
                    "ai_cache_read_after_race_failed"
                ) from None
            accepted = (
                _entry_from_document(stored, entry.identity, entry.created_at)
                if stored is not None
                else None
            )
            if accepted is None:
                if stored is None:
                    raise AICacheRepositoryError("ai_cache_conflicting_entry") from None
                try:
                    replacement = await self._collection.replace_one(
                        {
                            "_id": entry.identity.cache_key,
                            "result": stored.get("result"),
                        },
                        document,
                    )
                except PyMongoError:
                    raise AICacheRepositoryError(
                        "ai_cache_invalid_entry_replacement_failed"
                    ) from None
                if replacement.modified_count == 1:
                    return AICacheWriteResult(entry=entry, created=True)
                raise AICacheRepositoryError("ai_cache_race_unresolved") from None
            return AICacheWriteResult(entry=accepted, created=False)
        except PyMongoError:
            raise AICacheRepositoryError("ai_cache_write_failed") from None


async def initialize_ai_cache_indexes(
    collection: AsyncCollection[MongoDocument],
) -> None:
    """Create unique identity and disposable expiry indexes."""
    try:
        await collection.create_index(
            [("cache_key", ASCENDING)],
            name="uq_ai_result_cache_key_v1",
            unique=True,
        )
        await collection.create_index(
            [("expires_at", ASCENDING)],
            name="ttl_ai_result_cache_expires_at_v1",
            expireAfterSeconds=0,
        )
    except PyMongoError:
        raise AICacheRepositoryError("ai_cache_index_initialization_failed") from None


__all__ = (
    "AI_CACHE_SCHEMA_VERSION",
    "MongoAICacheRepository",
    "initialize_ai_cache_indexes",
)
