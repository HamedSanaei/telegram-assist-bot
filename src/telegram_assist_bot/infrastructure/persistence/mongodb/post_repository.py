"""Asynchronous MongoDB implementation of the application post repository."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    InsertPostResult,
    InvalidPostRepositoryRequestError,
    PostClaimOutcome,
    PostClaimRequest,
    PostClaimResult,
    PostConcurrencyConflictError,
    PostNotFoundError,
    PostRepositoryDataError,
    PostRepositoryUnavailableError,
    PostTransitionRequest,
)
from telegram_assist_bot.domain.posts import Post, PostId, SourceMessageIdentity
from telegram_assist_bot.infrastructure.persistence.mongodb.errors import (
    InvalidPostDocumentError,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.post_mapper import (
    POST_DOCUMENT_SCHEMA_VERSION,
    post_from_document,
    post_to_document,
    status_transition_to_document,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

_DUPLICATE_KEY_ERROR_CODE: Final[int] = 11000
_MAX_LIST_LIMIT: Final[int] = 1000
_SOURCE_IDENTITY_KEY_PATTERN: Final[tuple[tuple[str, int], ...]] = (
    ("source_channel_id", ASCENDING),
    ("source_message_id", ASCENDING),
)
_INTERNAL_ID_KEY_PATTERN: Final[tuple[tuple[str, int], ...]] = (("_id", ASCENDING),)


def _canonical_as_of(value: datetime) -> datetime:
    """Validate a request timestamp and canonicalize its instant to UTC."""
    if type(value) is not datetime:
        raise InvalidPostRepositoryRequestError
    try:
        if value.tzinfo is None or value.utcoffset() is None:
            raise InvalidPostRepositoryRequestError
        return value.astimezone(UTC)
    except (OverflowError, TypeError, ValueError):
        raise InvalidPostRepositoryRequestError from None


def _floor_to_millisecond(value: datetime) -> datetime:
    """Encode an inclusive read boundary at BSON millisecond precision."""
    remainder = value.microsecond % 1000
    return value.replace(microsecond=value.microsecond - remainder)


def _duplicate_key_pattern(
    error: DuplicateKeyError,
) -> tuple[tuple[str, int], ...] | None:
    """Return one validated duplicate-key pattern without exposing its values."""
    if error.code != _DUPLICATE_KEY_ERROR_CODE or not isinstance(
        error.details, Mapping
    ):
        return None
    raw_pattern = error.details.get("keyPattern")
    if not isinstance(raw_pattern, Mapping):
        return None
    pattern: list[tuple[str, int]] = []
    for field_name, direction in raw_pattern.items():
        if (
            type(field_name) is not str
            or not isinstance(direction, int)
            or isinstance(direction, bool)
        ):
            return None
        pattern.append((field_name, int(direction)))
    return tuple(pattern)


def _is_source_identity_duplicate(error: DuplicateKeyError) -> bool:
    """Recognize only the compound source identity duplicate-key contract."""
    return _duplicate_key_pattern(error) == _SOURCE_IDENTITY_KEY_PATTERN


def _is_internal_id_duplicate(error: DuplicateKeyError) -> bool:
    """Recognize the MongoDB-owned identifier index for safe diagnosis."""
    return _duplicate_key_pattern(error) == _INTERNAL_ID_KEY_PATTERN


def _restore_post(document: object) -> Post:
    """Map a driver document to Domain without leaking mapping failures."""
    failed = False
    post: Post | None = None
    try:
        post = post_from_document(document)
    except InvalidPostDocumentError:
        failed = True
    if failed or post is None:
        raise PostRepositoryDataError
    return post


def _same_source_payload(existing: Post, candidate: Post) -> bool:
    """Compare immutable source facts while ignoring local receipt metadata."""
    return (
        existing.source_identity == candidate.source_identity
        and existing.source_channel_username == candidate.source_channel_username
        and existing.source_channel_display_name
        == candidate.source_channel_display_name
        and existing.original_content == candidate.original_content
        and existing.source_published_at == candidate.source_published_at
    )


@dataclass(slots=True)
class MongoPostRepository:
    """Persist post documents with database-enforced idempotency and CAS."""

    _collection: AsyncCollection[MongoDocument] = field(repr=False)
    _timeout_seconds: int

    def __post_init__(self) -> None:
        """Require the bounded timeout range already accepted by configuration."""
        if (
            type(self._timeout_seconds) is not int
            or not 1 <= self._timeout_seconds <= 120
        ):
            raise InvalidPostRepositoryRequestError

    async def insert_idempotently(self, post: Post) -> InsertPostResult:
        """Insert directly and map only the source unique race to AlreadyExists."""
        if type(post) is not Post:
            raise InvalidPostRepositoryRequestError
        document = post_to_document(post)
        outcome: InsertPostOutcome | None = None
        canonical_post_id: PostId | None = None
        unavailable = False
        data_conflict = False
        diagnose_identifier_conflict = False
        diagnose_source_conflict = False
        try:
            async with asyncio.timeout(self._timeout_seconds):
                await self._collection.insert_one(document)
            outcome = InsertPostOutcome.CREATED
            canonical_post_id = post.post_id
        except DuplicateKeyError as error:
            if _is_source_identity_duplicate(error):
                diagnose_source_conflict = True
            elif _is_internal_id_duplicate(error):
                diagnose_identifier_conflict = True
            else:
                data_conflict = True
        except (PyMongoError, TimeoutError):
            unavailable = True

        if diagnose_identifier_conflict or diagnose_source_conflict:
            existing_document: MongoDocument | None = None
            try:
                async with asyncio.timeout(self._timeout_seconds):
                    existing_document = await self._collection.find_one(
                        {"_id": post.post_id.value}
                        if diagnose_identifier_conflict
                        else {
                            "source_channel_id": (
                                post.source_identity.source_channel_id
                            ),
                            "source_message_id": (
                                post.source_identity.source_message_id
                            ),
                        }
                    )
            except (PyMongoError, TimeoutError):
                unavailable = True
            if not unavailable and existing_document is not None:
                existing = _restore_post(existing_document)
                if existing.source_identity == post.source_identity:
                    canonical_post_id = existing.post_id
                    outcome = (
                        InsertPostOutcome.ALREADY_EXISTS
                        if _same_source_payload(existing, post)
                        else InsertPostOutcome.CONFLICT
                    )
                else:
                    data_conflict = True
            elif not unavailable:
                data_conflict = True

        if data_conflict:
            raise PostRepositoryDataError
        if unavailable or outcome is None or canonical_post_id is None:
            raise PostRepositoryUnavailableError
        return InsertPostResult(outcome, canonical_post_id)

    async def claim_for_next_stage(self, request: PostClaimRequest) -> PostClaimResult:
        """Atomically set one durable next-stage marker outside Domain state."""
        if type(request) is not PostClaimRequest:
            raise InvalidPostRepositoryRequestError
        claimed_at = _canonical_as_of(request.claimed_at)
        persisted_claimed_at = _floor_to_millisecond(claimed_at)
        query: MongoDocument = {
            "_id": request.post_id.value,
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "source_channel_id": request.source_identity.source_channel_id,
            "source_message_id": request.source_identity.source_message_id,
            "status": "Stored",
            "next_stage_claimed_at": None,
        }
        update: MongoDocument = {
            "$set": {
                "next_stage_claimed_at": persisted_claimed_at,
                "next_stage_claim_correlation_id": request.correlation_id,
            }
        }
        updated: MongoDocument | None = None
        current: MongoDocument | None = None
        unavailable = False
        try:
            async with asyncio.timeout(self._timeout_seconds):
                updated = await self._collection.find_one_and_update(
                    query,
                    update,
                    upsert=False,
                    return_document=ReturnDocument.AFTER,
                )
                if updated is None:
                    current = await self._collection.find_one(
                        {"_id": request.post_id.value}
                    )
        except (PyMongoError, TimeoutError):
            unavailable = True
        if unavailable:
            raise PostRepositoryUnavailableError
        if updated is not None:
            _restore_post(updated)
            return PostClaimResult(PostClaimOutcome.CLAIMED, request.post_id)
        if current is None:
            raise PostNotFoundError
        current_post = _restore_post(current)
        if current_post.source_identity != request.source_identity:
            return PostClaimResult(PostClaimOutcome.CONFLICT, current_post.post_id)
        if current.get("next_stage_claimed_at") is not None:
            return PostClaimResult(
                PostClaimOutcome.ALREADY_CLAIMED, current_post.post_id
            )
        return PostClaimResult(PostClaimOutcome.CONFLICT, current_post.post_id)

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        """Return one visible, non-expired post by its application identifier."""
        if type(post_id) is not PostId:
            raise InvalidPostRepositoryRequestError
        return await self._find_one_unexpired(
            {"_id": post_id.value},
            as_of=_canonical_as_of(as_of),
        )

    async def get_by_source_identity(
        self,
        source_identity: SourceMessageIdentity,
        *,
        as_of: datetime,
    ) -> Post | None:
        """Return one visible post by its stable source idempotency identity."""
        if type(source_identity) is not SourceMessageIdentity:
            raise InvalidPostRepositoryRequestError
        return await self._find_one_unexpired(
            {
                "source_channel_id": source_identity.source_channel_id,
                "source_message_id": source_identity.source_message_id,
            },
            as_of=_canonical_as_of(as_of),
        )

    async def _find_one_unexpired(
        self,
        identity_filter: Mapping[str, object],
        *,
        as_of: datetime,
    ) -> Post | None:
        """Apply both the TTL-compatible query filter and exact Domain boundary."""
        query: MongoDocument = {
            **identity_filter,
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "expires_at": {"$gt": _floor_to_millisecond(as_of)},
        }
        document: MongoDocument | None = None
        unavailable = False
        try:
            async with asyncio.timeout(self._timeout_seconds):
                document = await self._collection.find_one(query)
        except (PyMongoError, TimeoutError):
            unavailable = True
        if unavailable:
            raise PostRepositoryUnavailableError
        if document is None:
            return None
        post = _restore_post(document)
        return None if post.is_expired_at(as_of) else post

    async def list_unexpired(
        self,
        *,
        as_of: datetime,
        limit: int,
    ) -> tuple[Post, ...]:
        """Return a deterministic bounded list after exact expiration filtering."""
        normalized_as_of = _canonical_as_of(as_of)
        if type(limit) is not int or not 1 <= limit <= _MAX_LIST_LIMIT:
            raise InvalidPostRepositoryRequestError
        query: MongoDocument = {
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "expires_at": {"$gt": _floor_to_millisecond(normalized_as_of)},
        }
        posts: list[Post] = []
        unavailable = False
        data_failed = False
        try:
            async with asyncio.timeout(self._timeout_seconds):
                cursor = self._collection.find(query).sort(
                    [("received_at", ASCENDING), ("_id", ASCENDING)]
                )
                async for document in cursor:
                    try:
                        post = post_from_document(document)
                    except InvalidPostDocumentError:
                        data_failed = True
                        break
                    if post.is_expired_at(normalized_as_of):
                        continue
                    posts.append(post)
                    if len(posts) == limit:
                        break
        except (PyMongoError, TimeoutError):
            unavailable = True

        if data_failed:
            raise PostRepositoryDataError
        if unavailable:
            raise PostRepositoryUnavailableError
        return tuple(posts)

    async def transition(self, request: PostTransitionRequest) -> Post:
        """Persist one lifecycle tail with an atomic version-and-status compare."""
        if type(request) is not PostTransitionRequest:
            raise InvalidPostRepositoryRequestError
        target = request.post
        transition_document = status_transition_to_document(
            target.transition_history[-1]
        )
        query: MongoDocument = {
            "_id": target.post_id.value,
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "version": request.expected_version,
            "status": request.expected_status.value,
        }
        update: MongoDocument = {
            "$set": {
                "status": target.status.value,
                "version": target.version,
            },
            "$push": {"transition_history": transition_document},
        }
        updated: MongoDocument | None = None
        current: MongoDocument | None = None
        unavailable = False
        try:
            async with asyncio.timeout(self._timeout_seconds):
                updated = await self._collection.find_one_and_update(
                    query,
                    update,
                    upsert=False,
                    return_document=ReturnDocument.AFTER,
                )
                if updated is None:
                    current = await self._collection.find_one(
                        {"_id": target.post_id.value}
                    )
        except (PyMongoError, TimeoutError):
            unavailable = True

        if unavailable:
            raise PostRepositoryUnavailableError
        if updated is None:
            if current is None:
                raise PostNotFoundError
            _restore_post(current)
            raise PostConcurrencyConflictError

        persisted = _restore_post(updated)
        if (
            persisted.post_id != target.post_id
            or persisted.version != target.version
            or persisted.status is not target.status
            or persisted.transition_history[-1] != target.transition_history[-1]
        ):
            raise PostRepositoryDataError
        return persisted


__all__ = ("MongoPostRepository",)
