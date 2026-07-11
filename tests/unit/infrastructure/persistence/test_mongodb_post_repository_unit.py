from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest
from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    InvalidPostRepositoryRequestError,
    PostClaimOutcome,
    PostClaimRequest,
    PostConcurrencyConflictError,
    PostNotFoundError,
    PostRepositoryDataError,
    PostRepositoryUnavailableError,
    PostTransitionRequest,
)
from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TelegramEntity,
    TransitionActorCategory,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.post_mapper import (
    POST_DOCUMENT_SCHEMA_VERSION,
    post_to_document,
    status_transition_to_document,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.post_repository import (
    MongoPostRepository,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

_SENSITIVE_SENTINEL = "mongodb://user:رمز@آزمایش.example.invalid/admin"


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


@dataclass(slots=True)
class _FakeCursor:
    documents: list[MongoDocument]
    iteration_failure: BaseException | None = None
    sort_calls: list[list[tuple[str, int]]] = field(default_factory=list)
    _position: int = 0

    def sort(self, keys: list[tuple[str, int]]) -> _FakeCursor:
        copied_keys = list(keys)
        self.sort_calls.append(copied_keys)
        if copied_keys != [
            ("received_at", ASCENDING),
            ("_id", ASCENDING),
        ]:
            raise AssertionError("Unexpected repository sort contract.")
        self.documents.sort(
            key=lambda document: (
                cast("datetime", document["received_at"]),
                cast("str", document["_id"]),
            )
        )
        return self

    def __aiter__(self) -> _FakeCursor:
        return self

    async def __anext__(self) -> MongoDocument:
        if self.iteration_failure is not None:
            failure = self.iteration_failure
            self.iteration_failure = None
            raise failure
        if self._position >= len(self.documents):
            raise StopAsyncIteration
        document = self.documents[self._position]
        self._position += 1
        return deepcopy(document)


@dataclass(slots=True)
class _FakeCollection:
    insert_failure: BaseException | None = None
    find_one_failure: BaseException | None = None
    find_failure: BaseException | None = None
    find_iteration_failure: BaseException | None = None
    update_failure: BaseException | None = None
    find_one_results: list[MongoDocument | None] = field(default_factory=list)
    find_documents: list[MongoDocument] = field(default_factory=list)
    update_result: MongoDocument | None = None
    inserted_documents: list[MongoDocument] = field(default_factory=list)
    find_one_queries: list[MongoDocument] = field(default_factory=list)
    find_queries: list[MongoDocument] = field(default_factory=list)
    update_calls: list[tuple[MongoDocument, MongoDocument, bool, object]] = field(
        default_factory=list
    )
    last_cursor: _FakeCursor | None = None

    async def insert_one(self, document: MongoDocument) -> object:
        self.inserted_documents.append(deepcopy(document))
        if self.insert_failure is not None:
            raise self.insert_failure
        return object()

    async def find_one(self, query: MongoDocument) -> MongoDocument | None:
        self.find_one_queries.append(deepcopy(query))
        if self.find_one_failure is not None:
            raise self.find_one_failure
        if not self.find_one_results:
            return None
        result = self.find_one_results.pop(0)
        return None if result is None else deepcopy(result)

    def find(self, query: MongoDocument) -> _FakeCursor:
        self.find_queries.append(deepcopy(query))
        if self.find_failure is not None:
            raise self.find_failure
        cursor = _FakeCursor(
            documents=deepcopy(self.find_documents),
            iteration_failure=self.find_iteration_failure,
        )
        self.last_cursor = cursor
        return cursor

    async def find_one_and_update(
        self,
        query: MongoDocument,
        update: MongoDocument,
        *,
        upsert: bool,
        return_document: object,
    ) -> MongoDocument | None:
        self.update_calls.append(
            (deepcopy(query), deepcopy(update), upsert, return_document)
        )
        if self.update_failure is not None:
            raise self.update_failure
        return None if self.update_result is None else deepcopy(self.update_result)


def _repository(collection: _FakeCollection) -> MongoPostRepository:
    typed_collection = cast("AsyncCollection[MongoDocument]", collection)
    return MongoPostRepository(typed_collection, _timeout_seconds=1)


def _make_post(
    *,
    post_id: str = "post-repository-1",
    source_message_id: int = 321,
    received_at: datetime | None = None,
) -> Post:
    received = received_at or datetime(
        2026,
        3,
        20,
        8,
        9,
        10,
        789123,
        tzinfo=UTC,
    )
    return Post(
        post_id=PostId(post_id),
        source_identity=SourceMessageIdentity(-1001234567890, source_message_id),
        source_channel_username="Exact_ChannelName",
        source_channel_display_name="کانال نمونه ✅",
        original_content=OriginalPostContent(
            text="سلام\nخط دوم با نیم‌فاصله و ایموجی ویژه ✨",
            caption="کپشن اصلی 🧿",
            text_entities=(
                TelegramEntity(0, 4, "bold"),
                TelegramEntity(33, 2, "custom_emoji", "5368324170671202286"),
            ),
            caption_entities=(TelegramEntity(0, 5, "italic"),),
        ),
        source_published_at=received - timedelta(minutes=2),
        received_at=received,
    )


def _stored_post(post: Post | None = None) -> Post:
    discovered = post or _make_post()
    return discovered.transition_to(
        PostStatus.STORED,
        expected_version=discovered.version,
        occurred_at=discovered.received_at + timedelta(seconds=1, microseconds=321),
        actor_category=TransitionActorCategory.SERVICE,
        reason="persisted_without_normalization",
        correlation_id="corr-t004-unit",
    )


def _source_duplicate_error() -> DuplicateKeyError:
    return DuplicateKeyError(
        f"duplicate source identity {_SENSITIVE_SENTINEL}",
        code=11000,
        details={
            "keyPattern": {
                "source_channel_id": ASCENDING,
                "source_message_id": ASCENDING,
            },
            "keyValue": {
                "source_channel_id": -1001234567890,
                "source_message_id": 321,
            },
            "errmsg": _SENSITIVE_SENTINEL,
        },
    )


def _internal_id_duplicate_error() -> DuplicateKeyError:
    return DuplicateKeyError(
        f"duplicate internal id {_SENSITIVE_SENTINEL}",
        code=11000,
        details={"keyPattern": {"_id": ASCENDING}, "errmsg": _SENSITIVE_SENTINEL},
    )


def _assert_safe_error(error: Exception) -> None:
    assert _SENSITIVE_SENTINEL not in str(error)
    assert _SENSITIVE_SENTINEL not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None
    assert vars(error) == {}


def test_insert_is_direct_and_returns_created_without_a_pre_read() -> None:
    post = _make_post()
    collection = _FakeCollection()

    result = _run(_repository(collection).insert_idempotently(post))

    assert result.outcome is InsertPostOutcome.CREATED
    assert collection.inserted_documents == [post_to_document(post)]
    assert collection.find_one_queries == []
    assert collection.find_queries == []
    assert collection.update_calls == []


def test_exact_source_identity_duplicate_is_an_idempotent_outcome() -> None:
    post = _make_post()
    collection = _FakeCollection(
        insert_failure=_source_duplicate_error(),
        find_one_results=[post_to_document(post)],
    )

    result = _run(_repository(collection).insert_idempotently(post))

    assert result.outcome is InsertPostOutcome.ALREADY_EXISTS
    assert result.post_id == post.post_id
    assert len(collection.inserted_documents) == 1
    assert collection.find_one_queries == [
        {
            "source_channel_id": post.source_identity.source_channel_id,
            "source_message_id": post.source_identity.source_message_id,
        }
    ]


def test_same_identity_with_different_source_payload_is_conflict() -> None:
    incoming = _make_post()
    existing = replace(incoming, source_channel_display_name="Different source")
    collection = _FakeCollection(
        insert_failure=_source_duplicate_error(),
        find_one_results=[post_to_document(existing)],
    )

    result = _run(_repository(collection).insert_idempotently(incoming))

    assert result.outcome is InsertPostOutcome.CONFLICT
    assert result.post_id == existing.post_id


def test_next_stage_claim_is_one_atomic_conditional_update() -> None:
    post = _stored_post()
    claimed_at = post.received_at + timedelta(seconds=2)
    updated = post_to_document(post)
    persisted_claimed_at = claimed_at.replace(microsecond=789000)
    updated["next_stage_claimed_at"] = persisted_claimed_at
    updated["next_stage_claim_correlation_id"] = "corr-claim"
    collection = _FakeCollection(update_result=updated)
    request = PostClaimRequest(
        post.post_id,
        post.source_identity,
        claimed_at,
        "corr-claim",
    )

    result = _run(_repository(collection).claim_for_next_stage(request))

    assert result.outcome is PostClaimOutcome.CLAIMED
    assert result.post_id == post.post_id
    query, update, upsert, return_document = collection.update_calls[0]
    assert query["next_stage_claimed_at"] is None
    assert query["status"] == "Stored"
    assert update["$set"] == {
        "next_stage_claimed_at": persisted_claimed_at,
        "next_stage_claim_correlation_id": "corr-claim",
    }
    assert upsert is False
    assert return_document is ReturnDocument.AFTER


def test_losing_next_stage_claim_returns_already_claimed() -> None:
    post = _stored_post()
    claimed = post_to_document(post)
    claimed["next_stage_claimed_at"] = (
        post.received_at + timedelta(seconds=2)
    ).replace(microsecond=789000)
    claimed["next_stage_claim_correlation_id"] = "winner"
    collection = _FakeCollection(find_one_results=[claimed])
    request = PostClaimRequest(
        post.post_id,
        post.source_identity,
        post.received_at + timedelta(seconds=3),
        "loser",
    )

    result = _run(_repository(collection).claim_for_next_stage(request))

    assert result.outcome is PostClaimOutcome.ALREADY_CLAIMED
    assert result.post_id == post.post_id


def test_internal_id_race_is_idempotent_only_after_source_identity_diagnosis() -> None:
    post = _make_post()
    collection = _FakeCollection(
        insert_failure=_internal_id_duplicate_error(),
        find_one_results=[post_to_document(post)],
    )

    result = _run(_repository(collection).insert_idempotently(post))

    assert result.outcome is InsertPostOutcome.ALREADY_EXISTS
    assert collection.find_one_queries == [{"_id": post.post_id.value}]


def test_internal_id_collision_with_another_source_is_a_safe_data_error() -> None:
    incoming = _make_post()
    unrelated = _make_post(source_message_id=999)
    collection = _FakeCollection(
        insert_failure=_internal_id_duplicate_error(),
        find_one_results=[post_to_document(unrelated)],
    )

    with pytest.raises(PostRepositoryDataError) as captured:
        _run(_repository(collection).insert_idempotently(incoming))

    _assert_safe_error(captured.value)


@pytest.mark.parametrize(
    "duplicate_error",
    [
        DuplicateKeyError(
            f"wrong duplicate code {_SENSITIVE_SENTINEL}",
            code=11001,
            details={
                "keyPattern": {
                    "source_channel_id": ASCENDING,
                    "source_message_id": ASCENDING,
                },
                "errmsg": _SENSITIVE_SENTINEL,
            },
        ),
        DuplicateKeyError(
            f"wrong compound order {_SENSITIVE_SENTINEL}",
            code=11000,
            details={
                "keyPattern": {
                    "source_message_id": ASCENDING,
                    "source_channel_id": ASCENDING,
                },
                "errmsg": _SENSITIVE_SENTINEL,
            },
        ),
    ],
    ids=("unrelated-code", "different-key-order"),
)
def test_unrelated_duplicate_keys_map_to_safe_data_errors(
    duplicate_error: DuplicateKeyError,
) -> None:
    collection = _FakeCollection(insert_failure=duplicate_error)

    with pytest.raises(PostRepositoryDataError) as captured:
        _run(_repository(collection).insert_idempotently(_make_post()))

    _assert_safe_error(captured.value)


def test_internal_id_diagnosis_driver_failure_is_a_safe_availability_error() -> None:
    collection = _FakeCollection(
        insert_failure=_internal_id_duplicate_error(),
        find_one_failure=PyMongoError(_SENSITIVE_SENTINEL),
    )

    with pytest.raises(PostRepositoryUnavailableError) as captured:
        _run(_repository(collection).insert_idempotently(_make_post()))

    _assert_safe_error(captured.value)


def test_repository_repr_does_not_expose_collection_details() -> None:
    collection = _FakeCollection(find_failure=PyMongoError(_SENSITIVE_SENTINEL))

    representation = repr(_repository(collection))

    assert "_collection" not in representation
    assert _SENSITIVE_SENTINEL not in representation


@pytest.mark.parametrize("failure_kind", ["driver", "timeout"])
@pytest.mark.parametrize("operation", ["insert", "get", "list", "transition"])
def test_driver_and_timeout_failures_are_redacted_availability_errors(
    operation: str,
    failure_kind: str,
) -> None:
    failure: BaseException
    if failure_kind == "driver":
        failure = PyMongoError(_SENSITIVE_SENTINEL)
    else:
        failure = TimeoutError(_SENSITIVE_SENTINEL)
    collection = _FakeCollection()
    post = _make_post()
    repository = _repository(collection)
    invocation: Coroutine[Any, Any, object]

    if operation == "insert":
        collection.insert_failure = failure
        invocation = repository.insert_idempotently(post)
    elif operation == "get":
        collection.find_one_failure = failure
        invocation = repository.get_by_id(post.post_id, as_of=post.received_at)
    elif operation == "list":
        collection.find_failure = failure
        invocation = repository.list_unexpired(as_of=post.received_at, limit=10)
    else:
        target = _stored_post(post)
        collection.update_failure = failure
        invocation = repository.transition(
            PostTransitionRequest(
                post=target,
                expected_version=0,
                expected_status=PostStatus.DISCOVERED,
            )
        )

    with pytest.raises(PostRepositoryUnavailableError) as captured:
        _run(invocation)

    _assert_safe_error(captured.value)


def test_get_by_id_uses_schema_and_coarse_expiration_filter() -> None:
    post = _make_post()
    as_of = post.expires_at - timedelta(microseconds=1)
    collection = _FakeCollection(find_one_results=[post_to_document(post)])

    restored = _run(_repository(collection).get_by_id(post.post_id, as_of=as_of))

    assert restored is not None
    assert post_to_document(restored) == post_to_document(post)
    assert collection.find_one_queries == [
        {
            "_id": post.post_id.value,
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "expires_at": {
                "$gt": as_of.replace(microsecond=as_of.microsecond // 1000 * 1000)
            },
        }
    ]


def test_get_by_source_identity_uses_the_exact_compound_identity() -> None:
    post = _make_post()
    as_of = post.received_at + timedelta(hours=1, microseconds=999)
    collection = _FakeCollection(find_one_results=[post_to_document(post)])

    restored = _run(
        _repository(collection).get_by_source_identity(
            post.source_identity,
            as_of=as_of,
        )
    )

    assert restored is not None
    assert restored.source_identity == post.source_identity
    assert collection.find_one_queries == [
        {
            "source_channel_id": post.source_identity.source_channel_id,
            "source_message_id": post.source_identity.source_message_id,
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "expires_at": {
                "$gt": as_of.replace(microsecond=as_of.microsecond // 1000 * 1000)
            },
        }
    ]


def test_get_applies_the_exact_domain_expiration_boundary_before_ttl_sweep() -> None:
    post = _make_post()
    document = post_to_document(post)
    collection = _FakeCollection(find_one_results=[document, document])
    repository = _repository(collection)

    before_boundary = _run(
        repository.get_by_id(
            post.post_id,
            as_of=post.expires_at - timedelta(microseconds=1),
        )
    )
    at_boundary = _run(repository.get_by_id(post.post_id, as_of=post.expires_at))

    assert before_boundary is not None
    assert at_boundary is None


def test_get_maps_corrupt_documents_to_a_content_safe_data_error() -> None:
    corrupt_document = post_to_document(_make_post())
    corrupt_document["status"] = _SENSITIVE_SENTINEL
    collection = _FakeCollection(find_one_results=[corrupt_document])

    with pytest.raises(PostRepositoryDataError) as captured:
        _run(
            _repository(collection).get_by_id(
                PostId("post-repository-1"),
                as_of=datetime(2026, 3, 20, tzinfo=UTC),
            )
        )

    _assert_safe_error(captured.value)


@pytest.mark.parametrize("invalid_limit", [0, -1, 1001, True, 1.5])
def test_list_rejects_invalid_limits_before_touching_the_collection(
    invalid_limit: object,
) -> None:
    collection = _FakeCollection()

    with pytest.raises(InvalidPostRepositoryRequestError):
        _run(
            _repository(collection).list_unexpired(
                as_of=datetime(2026, 7, 11, tzinfo=UTC),
                limit=cast("int", invalid_limit),
            )
        )

    assert collection.find_queries == []


def test_list_is_sorted_bounded_and_exactly_filters_expired_documents() -> None:
    as_of = datetime(2026, 7, 11, 12, 0, 0, 789123, tzinfo=UTC)
    expired = _make_post(
        post_id="expired",
        source_message_id=1,
        received_at=as_of - timedelta(days=14),
    )
    later_id = _make_post(
        post_id="post-z",
        source_message_id=2,
        received_at=as_of - timedelta(days=2),
    )
    earlier_id = _make_post(
        post_id="post-a",
        source_message_id=3,
        received_at=as_of - timedelta(days=2),
    )
    collection = _FakeCollection(
        find_documents=[
            post_to_document(later_id),
            post_to_document(expired),
            post_to_document(earlier_id),
        ]
    )

    posts = _run(
        _repository(collection).list_unexpired(
            as_of=as_of,
            limit=2,
        )
    )

    assert tuple(post.post_id.value for post in posts) == ("post-a", "post-z")
    assert collection.find_queries == [
        {
            "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
            "expires_at": {
                "$gt": as_of.replace(microsecond=as_of.microsecond // 1000 * 1000)
            },
        }
    ]
    assert collection.last_cursor is not None
    assert collection.last_cursor.sort_calls == [
        [("received_at", ASCENDING), ("_id", ASCENDING)]
    ]


def test_list_maps_corrupt_documents_to_data_error() -> None:
    corrupt_document = post_to_document(_make_post())
    del corrupt_document["original_content"]
    collection = _FakeCollection(find_documents=[corrupt_document])

    with pytest.raises(PostRepositoryDataError) as captured:
        _run(
            _repository(collection).list_unexpired(
                as_of=datetime(2026, 3, 20, tzinfo=UTC),
                limit=10,
            )
        )

    _assert_safe_error(captured.value)


def test_transition_uses_exact_compare_and_set_and_returns_mapped_snapshot() -> None:
    discovered = _make_post()
    target = _stored_post(discovered)
    collection = _FakeCollection(update_result=post_to_document(target))
    request = PostTransitionRequest(
        post=target,
        expected_version=0,
        expected_status=PostStatus.DISCOVERED,
    )

    restored = _run(_repository(collection).transition(request))

    assert post_to_document(restored) == post_to_document(target)
    assert collection.update_calls == [
        (
            {
                "_id": target.post_id.value,
                "schema_version": POST_DOCUMENT_SCHEMA_VERSION,
                "version": 0,
                "status": "Discovered",
            },
            {
                "$set": {"status": "Stored", "version": 1},
                "$push": {
                    "transition_history": status_transition_to_document(
                        target.transition_history[-1]
                    )
                },
            },
            False,
            ReturnDocument.AFTER,
        )
    ]
    assert collection.find_one_queries == []


def test_failed_transition_with_an_existing_post_is_a_concurrency_conflict() -> None:
    discovered = _make_post()
    target = _stored_post(discovered)
    collection = _FakeCollection(
        update_result=None,
        find_one_results=[post_to_document(discovered)],
    )

    with pytest.raises(PostConcurrencyConflictError) as captured:
        _run(
            _repository(collection).transition(
                PostTransitionRequest(
                    post=target,
                    expected_version=0,
                    expected_status=PostStatus.DISCOVERED,
                )
            )
        )

    _assert_safe_error(captured.value)
    assert collection.find_one_queries == [{"_id": target.post_id.value}]


def test_failed_transition_with_no_current_post_is_not_found() -> None:
    target = _stored_post()
    collection = _FakeCollection(update_result=None, find_one_results=[None])

    with pytest.raises(PostNotFoundError) as captured:
        _run(
            _repository(collection).transition(
                PostTransitionRequest(
                    post=target,
                    expected_version=0,
                    expected_status=PostStatus.DISCOVERED,
                )
            )
        )

    _assert_safe_error(captured.value)


def test_failed_transition_rejects_a_corrupt_current_document() -> None:
    target = _stored_post()
    corrupt_current = post_to_document(_make_post())
    corrupt_current["schema_version"] = 999
    collection = _FakeCollection(
        update_result=None,
        find_one_results=[corrupt_current],
    )

    with pytest.raises(PostRepositoryDataError) as captured:
        _run(
            _repository(collection).transition(
                PostTransitionRequest(
                    post=target,
                    expected_version=0,
                    expected_status=PostStatus.DISCOVERED,
                )
            )
        )

    _assert_safe_error(captured.value)


def test_transition_rejects_a_corrupt_or_unrelated_returned_snapshot() -> None:
    target = _stored_post()
    unrelated = _stored_post(_make_post(post_id="unrelated", source_message_id=999))
    collection = _FakeCollection(update_result=post_to_document(unrelated))

    with pytest.raises(PostRepositoryDataError) as captured:
        _run(
            _repository(collection).transition(
                PostTransitionRequest(
                    post=target,
                    expected_version=0,
                    expected_status=PostStatus.DISCOVERED,
                )
            )
        )

    _assert_safe_error(captured.value)
