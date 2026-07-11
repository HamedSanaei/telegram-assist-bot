"""Exercise MongoDB post persistence against an isolated real test database."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Protocol, cast
from uuid import uuid4

import pytest
from pymongo import ASCENDING, AsyncMongoClient
from tests.integration.infrastructure.persistence.conftest import (
    MongoTestSettings as CleanupMongoTestSettings,
)
from tests.integration.infrastructure.persistence.conftest import (
    drop_test_database,
)

from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    PostConcurrencyConflictError,
    PostNotFoundError,
    PostRepositoryDataError,
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
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    POST_EXPIRATION_INDEX_NAME,
    POST_SOURCE_IDENTITY_INDEX_NAME,
    MongoConnectionError,
    MongoIndexInitializationError,
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    initialize_post_indexes,
    post_to_document,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

pytestmark = pytest.mark.integration

_MONGODB_URI_ENVIRONMENT_NAME = "TEST_MONGODB_URI"
_RECEIVED_AT = datetime(2099, 3, 20, 8, 9, 10, 789123, tzinfo=UTC)


class MongoTestSettings(Protocol):
    """Describe the safe fixture fields consumed by integration scenarios."""

    uri: str
    database_name: str


@dataclass(slots=True)
class _MongoResources:
    client: AsyncMongoClient[MongoDocument]
    collection: AsyncCollection[MongoDocument]
    repository: MongoPostRepository
    config: MongoConfig


def _mongo_config(
    settings: MongoTestSettings, *, timeout_seconds: int = 5
) -> MongoConfig:
    reference = SecretReference(environment_variable=_MONGODB_URI_ENVIRONMENT_NAME)
    return MongoConfig(
        uri=reference,
        database_name=settings.database_name,
        connect_timeout_seconds=timeout_seconds,
    )


@asynccontextmanager
async def _resources(
    settings: MongoTestSettings,
    *,
    initialize_indexes: bool = True,
) -> AsyncIterator[_MongoResources]:
    config = _mongo_config(settings)
    secrets = ResolvedSecrets({_MONGODB_URI_ENVIRONMENT_NAME: settings.uri})
    client = create_mongodb_client(config, secrets)
    try:
        await verify_mongodb_connection(
            client,
            timeout_seconds=config.connect_timeout_seconds,
        )
        collection = get_posts_collection(client, config)
        if initialize_indexes:
            await initialize_post_indexes(
                collection,
                timeout_seconds=config.connect_timeout_seconds,
            )
        yield _MongoResources(
            client=client,
            collection=collection,
            repository=MongoPostRepository(
                collection,
                config.connect_timeout_seconds,
            ),
            config=config,
        )
    finally:
        await close_mongodb_client(
            client,
            timeout_seconds=config.connect_timeout_seconds,
        )


def _make_post(
    *,
    post_id: str = "post-t004-1",
    source_channel_id: int = -1001234567890,
    source_message_id: int = 321,
    text: str = "سلام\nخبر تازه با نیم‌فاصله 👨‍💻 ✨",
    received_at: datetime = _RECEIVED_AT,
) -> Post:
    return Post(
        post_id=PostId(post_id),
        source_identity=SourceMessageIdentity(
            source_channel_id,
            source_message_id,
        ),
        source_channel_username="Exact_ChannelName",
        source_channel_display_name="کانال نمونه ✅",
        original_content=OriginalPostContent(
            text=text,
            caption="کپشن اصلی\nبدون تغییر 🧿",
            text_entities=(
                TelegramEntity(0, 4, "bold"),
                TelegramEntity(31, 2, "custom_emoji", "5368324170671202286"),
            ),
            caption_entities=(TelegramEntity(0, 5, "italic"),),
        ),
        source_published_at=received_at - timedelta(minutes=2, microseconds=456),
        received_at=received_at,
    )


def _stored(post: Post) -> Post:
    return post.transition_to(
        PostStatus.STORED,
        expected_version=post.version,
        occurred_at=post.received_at + timedelta(seconds=1, microseconds=321),
        actor_category=TransitionActorCategory.SERVICE,
        reason="ذخیره شد بدون تغییر متن",
        correlation_id="corr-01",
    )


def _assert_post_fields_equal(actual: Post, expected: Post) -> None:
    assert actual.post_id == expected.post_id
    assert actual.source_identity == expected.source_identity
    assert actual.source_channel_username == expected.source_channel_username
    assert actual.source_channel_display_name == expected.source_channel_display_name
    assert actual.original_content == expected.original_content
    assert actual.source_published_at == expected.source_published_at
    assert actual.received_at == expected.received_at
    assert actual.expires_at == expected.expires_at
    assert actual.status is expected.status
    assert actual.version == expected.version
    assert actual.transition_history == expected.transition_history


def test_index_initializer_is_repeatable_and_creates_exact_specs(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(
            mongodb_test_settings,
            initialize_indexes=False,
        ) as resources:
            await initialize_post_indexes(resources.collection, timeout_seconds=5)
            await initialize_post_indexes(resources.collection, timeout_seconds=5)
            cursor = await resources.collection.list_indexes()
            documents = await cursor.to_list()
            indexes = {
                cast("str", document["name"]): document for document in documents
            }

            assert set(indexes) == {
                "_id_",
                POST_SOURCE_IDENTITY_INDEX_NAME,
                POST_EXPIRATION_INDEX_NAME,
            }
            source = indexes[POST_SOURCE_IDENTITY_INDEX_NAME]
            assert tuple(cast("Mapping[str, int]", source["key"]).items()) == (
                ("source_channel_id", ASCENDING),
                ("source_message_id", ASCENDING),
            )
            assert source["unique"] is True
            expiration = indexes[POST_EXPIRATION_INDEX_NAME]
            assert tuple(cast("Mapping[str, int]", expiration["key"]).items()) == (
                ("expires_at", ASCENDING),
            )
            assert expiration["expireAfterSeconds"] == 0

    asyncio.run(scenario())


def test_cleanup_drops_only_the_selected_isolated_database(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        current = CleanupMongoTestSettings(
            uri=mongodb_test_settings.uri,
            database_name=mongodb_test_settings.database_name,
        )
        adjacent = CleanupMongoTestSettings(
            uri=mongodb_test_settings.uri,
            database_name=f"tab_t004_{uuid4().hex}",
        )
        config = _mongo_config(mongodb_test_settings)
        client = create_mongodb_client(
            config,
            ResolvedSecrets({_MONGODB_URI_ENVIRONMENT_NAME: mongodb_test_settings.uri}),
        )
        try:
            async with asyncio.timeout(config.connect_timeout_seconds):
                await client[current.database_name]["sentinel"].insert_one({"value": 1})
                await client[adjacent.database_name]["sentinel"].insert_one(
                    {"value": 2}
                )

            await drop_test_database(current)

            async with asyncio.timeout(config.connect_timeout_seconds):
                assert (
                    await client[current.database_name]["sentinel"].count_documents({})
                    == 0
                )
                assert (
                    await client[adjacent.database_name]["sentinel"].count_documents({})
                    == 1
                )
        finally:
            await drop_test_database(adjacent)
            await close_mongodb_client(
                client,
                timeout_seconds=config.connect_timeout_seconds,
            )

    asyncio.run(scenario())


def test_incompatible_index_fails_without_being_replaced(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(
            mongodb_test_settings,
            initialize_indexes=False,
        ) as resources:
            await resources.collection.create_index(
                [("expires_at", ASCENDING)],
                name=POST_EXPIRATION_INDEX_NAME,
                expireAfterSeconds=60,
            )

            with pytest.raises(MongoIndexInitializationError):
                await initialize_post_indexes(resources.collection, timeout_seconds=5)

            cursor = await resources.collection.list_indexes()
            documents = await cursor.to_list()
            expiration = next(
                document
                for document in documents
                if document["name"] == POST_EXPIRATION_INDEX_NAME
            )
            assert expiration["expireAfterSeconds"] == 60

    asyncio.run(scenario())


def test_insert_get_and_full_persian_round_trip(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            post = _make_post()

            result = await resources.repository.insert_idempotently(post)
            by_id = await resources.repository.get_by_id(
                post.post_id,
                as_of=post.received_at,
            )
            by_source = await resources.repository.get_by_source_identity(
                post.source_identity,
                as_of=post.received_at,
            )

            assert result.outcome is InsertPostOutcome.CREATED
            assert by_id is not None
            assert by_source is not None
            _assert_post_fields_equal(by_id, post)
            _assert_post_fields_equal(by_source, post)
            assert by_id.original_text == "سلام\nخبر تازه با نیم‌فاصله 👨‍💻 ✨"
            assert by_id.original_text_entities[1].custom_emoji_id == (
                "5368324170671202286"
            )
            assert by_id.received_at.tzinfo is UTC

    asyncio.run(scenario())


def test_duplicate_does_not_overwrite_original_document(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            original = _make_post()
            conflicting = _make_post(
                post_id="different-post-id",
                text="متن متفاوت که نباید جایگزین شود",
            )

            created = await resources.repository.insert_idempotently(original)
            duplicate = await resources.repository.insert_idempotently(conflicting)
            stored = await resources.repository.get_by_source_identity(
                original.source_identity,
                as_of=original.received_at,
            )

            assert created.outcome is InsertPostOutcome.CREATED
            assert duplicate.outcome is InsertPostOutcome.ALREADY_EXISTS
            assert await resources.collection.count_documents({}) == 1
            assert stored is not None
            _assert_post_fields_equal(stored, original)

    asyncio.run(scenario())


def test_concurrent_duplicate_inserts_have_one_deterministic_winner(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            post = _make_post()
            results = await asyncio.gather(
                *(resources.repository.insert_idempotently(post) for _ in range(20))
            )
            outcomes = [result.outcome for result in results]

            assert outcomes.count(InsertPostOutcome.CREATED) == 1
            assert outcomes.count(InsertPostOutcome.ALREADY_EXISTS) == 19
            assert await resources.collection.count_documents({}) == 1

    asyncio.run(scenario())


def test_duplicate_internal_id_is_not_mapped_to_source_idempotency(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            original = _make_post()
            unrelated = _make_post(
                source_channel_id=-1009999999999,
                source_message_id=999,
            )
            await resources.repository.insert_idempotently(original)

            with pytest.raises(PostRepositoryDataError):
                await resources.repository.insert_idempotently(unrelated)

            assert await resources.collection.count_documents({}) == 1

    asyncio.run(scenario())


def test_logical_expiration_filter_does_not_wait_for_ttl_monitor(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(
            mongodb_test_settings,
            initialize_indexes=False,
        ) as resources:
            expired = _make_post(
                received_at=datetime(2020, 1, 1, 0, 0, 0, 123456, tzinfo=UTC)
            )
            await resources.repository.insert_idempotently(expired)
            assert await resources.collection.count_documents({}) == 1

            by_id = await resources.repository.get_by_id(
                expired.post_id,
                as_of=expired.expires_at,
            )
            listed = await resources.repository.list_unexpired(
                as_of=expired.expires_at,
                limit=10,
            )

            assert by_id is None
            assert listed == ()
            assert await resources.collection.count_documents({}) == 1

    asyncio.run(scenario())


def test_atomic_transition_has_one_winner_and_preserves_original_content(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            original = _make_post()
            target = _stored(original)
            request = PostTransitionRequest(
                post=target,
                expected_version=original.version,
                expected_status=original.status,
            )
            await resources.repository.insert_idempotently(original)

            results = await asyncio.gather(
                resources.repository.transition(request),
                resources.repository.transition(request),
                return_exceptions=True,
            )
            successes = [result for result in results if isinstance(result, Post)]
            conflicts = [
                result
                for result in results
                if isinstance(result, PostConcurrencyConflictError)
            ]
            stored = await resources.repository.get_by_id(
                original.post_id,
                as_of=original.received_at,
            )

            assert len(successes) == 1
            assert len(conflicts) == 1
            assert stored is not None
            _assert_post_fields_equal(stored, target)
            assert stored.original_content == original.original_content
            assert len(stored.transition_history) == 1

    asyncio.run(scenario())


def test_stale_status_at_same_version_is_a_concurrency_conflict(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            initial = _make_post()
            direct_expired = initial.transition_to(
                PostStatus.EXPIRED,
                expected_version=0,
                occurred_at=initial.expires_at,
                actor_category=TransitionActorCategory.SERVICE,
                reason="direct expiration",
            )
            stored = _stored(initial)
            target = stored.transition_to(
                PostStatus.EXPIRED,
                expected_version=1,
                occurred_at=stored.expires_at,
                actor_category=TransitionActorCategory.SERVICE,
                reason="stored expiration",
            )
            request = PostTransitionRequest(
                post=target,
                expected_version=1,
                expected_status=PostStatus.STORED,
            )
            await resources.repository.insert_idempotently(direct_expired)

            with pytest.raises(PostConcurrencyConflictError):
                await resources.repository.transition(request)

    asyncio.run(scenario())


def test_transition_of_missing_post_is_distinct_from_conflict(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            initial = _make_post()
            target = _stored(initial)
            request = PostTransitionRequest(
                post=target,
                expected_version=0,
                expected_status=PostStatus.DISCOVERED,
            )

            with pytest.raises(PostNotFoundError):
                await resources.repository.transition(request)

    asyncio.run(scenario())


def test_connection_failure_is_bounded_and_redacted(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        user = "synthetic" + "-user"
        password = "synthetic" + "-password"
        uri = f"mongodb://{user}:{password}@127.0.0.1:1/?directConnection=true"
        reference = SecretReference(environment_variable=_MONGODB_URI_ENVIRONMENT_NAME)
        config = MongoConfig(
            uri=reference,
            database_name=mongodb_test_settings.database_name,
            connect_timeout_seconds=1,
        )
        client = create_mongodb_client(
            config,
            ResolvedSecrets({_MONGODB_URI_ENVIRONMENT_NAME: uri}),
        )
        try:
            with pytest.raises(MongoConnectionError) as error:
                await verify_mongodb_connection(client, timeout_seconds=1)
        finally:
            await close_mongodb_client(client, timeout_seconds=1)

        rendered = str(error.value) + repr(error.value)
        assert user not in rendered
        assert password not in rendered
        assert "127.0.0.1" not in rendered
        assert error.value.__cause__ is None
        assert error.value.__context__ is None

    asyncio.run(scenario())


def test_raw_document_stays_bson_compatible_and_source_fields_are_flat(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with _resources(mongodb_test_settings) as resources:
            post = _make_post()
            document = post_to_document(post)
            await resources.collection.insert_one(document)
            raw = await resources.collection.find_one({"_id": post.post_id.value})

            assert raw is not None
            assert raw["source_channel_id"] == post.source_identity.source_channel_id
            assert raw["source_message_id"] == post.source_identity.source_message_id
            assert raw["expires_at_microsecond_remainder"] == 123
            assert "post_id" not in raw

    asyncio.run(scenario())
