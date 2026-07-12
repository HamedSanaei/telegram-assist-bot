"""Exercise the complete non-live media runtime over real test MongoDB."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

import pytest

import telegram_assist_bot.bootstrap.text_ingestion as ingestion_module
from telegram_assist_bot.application.ports import (
    MediaTransientError,
    ResolvedTelegramChannel,
    TelegramAccount,
    TelegramChannelReference,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramMediaReference,
    TelegramTextMessage,
)
from telegram_assist_bot.bootstrap.text_ingestion import (
    FoundationLifecycle,
    TextIngestionApplication,
    TextIngestionDependencies,
    TextIngestionGateway,
    TextIngestionStartupError,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import PostId
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    initialize_post_indexes,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    ApplicationConfig,
    LoadedConfiguration,
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Mapping

    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

pytestmark = pytest.mark.integration
_URI_ENV = "TEST_MONGODB_URI"


class MongoTestSettings(Protocol):
    """Describe the guarded local MongoDB fixture."""

    uri: str
    database_name: str


@dataclass(frozen=True)
class Clock:
    """Return one deterministic time for crawl and preparation boundaries."""

    now: datetime

    def utc_now(self) -> datetime:
        return self.now


@dataclass
class Logger:
    """Capture only structured event metadata."""

    events: list[dict[str, object]] = field(default_factory=list)

    def emit(self, **event: object) -> None:
        self.events.append(dict(event))


@dataclass
class Foundation:
    """Own a real test client while injecting deterministic configuration."""

    configuration_value: LoadedConfiguration
    client: AsyncMongoClient[MongoDocument]
    repository_value: MongoPostRepository
    logger_value: Logger
    shutdown_calls: int = 0

    @property
    def repository(self) -> MongoPostRepository:
        return self.repository_value

    @property
    def logger(self) -> Logger:
        return self.logger_value

    @property
    def configuration(self) -> LoadedConfiguration:
        return self.configuration_value

    @property
    def mongodb_client(self) -> AsyncMongoClient[MongoDocument]:
        return self.client

    @property
    def correlation_id(self) -> str:
        return "t060-integration-correlation"

    async def start(
        self, configuration_path: Path, *, environ: Mapping[str, str] | None = None
    ) -> object:
        del configuration_path, environ
        return self

    async def shutdown(self) -> None:
        if self.shutdown_calls == 0:
            await close_mongodb_client(self.client, timeout_seconds=5)
        self.shutdown_calls += 1


class MediaSource:
    """Stream deterministic distinct payloads without a live provider."""

    def __init__(self) -> None:
        self.opens = 0

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        self.opens += 1
        payload = f"bytes:{opaque_reference}".encode()

        async def stream() -> AsyncIterator[bytes]:
            yield payload[:4]
            yield payload[4:]

        return stream()


class FailingMediaSource(MediaSource):
    """Interrupt every configured attempt after one streamed chunk."""

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        self.opens += 1

        async def stream() -> AsyncIterator[bytes]:
            yield f"partial:{opaque_reference}".encode()
            raise MediaTransientError("Synthetic interrupted media stream.")

        return stream()


@dataclass
class Subscription:
    """Deliver a finite prefix, then remain cancellable like a live stream."""

    items: list[TelegramTextMessage]
    closed: asyncio.Event = field(default_factory=asyncio.Event)
    close_calls: int = 0

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> TelegramTextMessage:
        if self.items:
            return self.items.pop(0)
        await self.closed.wait()
        raise StopAsyncIteration

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


@dataclass
class Gateway:
    """Combine fake validation/history/live and one shared media streamer."""

    history: tuple[TelegramTextMessage, ...]
    live: list[TelegramTextMessage]
    source: MediaSource
    close_calls: int = 0
    subscription: Subscription | None = None

    async def validate_account(self) -> TelegramAccount:
        return TelegramAccount(42, True)

    async def resolve_channel(
        self, reference: TelegramChannelReference
    ) -> ResolvedTelegramChannel:
        identifier = (
            -100
            if reference.configuration_path.startswith("source_channels")
            else reference.configured_channel_id or -200
        )
        return ResolvedTelegramChannel(
            identifier,
            reference.configured_username,
            reference.config_name,
            True,
            True,
            ()
            if reference.configured_username is None
            else (reference.configured_username,),
        )

    def register_channel(self, channel: ResolvedTelegramChannel) -> None:
        del channel

    async def open(self) -> None:
        return None

    def media_source(self) -> MediaSource:
        return self.source

    async def subscribe(
        self, source_channel_id: int, *, buffer_size: int
    ) -> Subscription:
        del source_channel_id, buffer_size
        self.subscription = Subscription(list(self.live))
        return self.subscription

    async def iter_history_pages(
        self, query: TelegramHistoryQuery
    ) -> AsyncIterator[TelegramHistoryPage]:
        del query
        yield TelegramHistoryPage(self.history)

    async def close(self) -> None:
        self.close_calls += 1


def source_message(
    message_id: int,
    at: datetime,
    *,
    media_type: MediaType | None = None,
    group_id: str | None = None,
    caption: str | None = None,
) -> TelegramTextMessage:
    """Build an exact fake Telegram DTO with no SDK values."""
    descriptors = (
        (
            TelegramMediaReference(
                media_type,
                0,
                64,
                "application/octet-stream",
                "رسانه.bin",
                f"opaque-{message_id}",
                group_id,
            ),
        )
        if media_type is not None
        else ()
    )
    return TelegramTextMessage(
        -100,
        "source_example",
        "منبع فارسی",
        message_id,
        "متن‌فارسی\n😀" if media_type is None else None,
        caption if media_type is not None else None,
        (),
        (),
        at,
        False,
        media_type is not None,
        descriptors,
    )


def loaded_configuration(tmp_path: Path, database_name: str) -> LoadedConfiguration:
    """Load only the safe example and redirect all runtime state to test roots."""
    root = Path(__file__).resolve().parents[2]
    settings = ApplicationConfig.model_validate_json(
        (root / "config" / "configuration.example.json").read_text(encoding="utf-8")
    )
    settings = settings.model_copy(
        update={
            "mongodb": settings.mongodb.model_copy(
                update={"database_name": database_name}
            ),
            "media": settings.media.model_copy(update={"root": tmp_path / "media"}),
        }
    )
    return LoadedConfiguration(
        settings,
        ResolvedSecrets(
            {
                "TAB_MONGODB_URI": "mongodb://127.0.0.1:27017",
                "TAB_TELEGRAM_API_ID": "123456",
                "TAB_TELEGRAM_API_HASH": "synthetic-api-hash",
                "TAB_TELEGRAM_PHONE_NUMBER": "synthetic-phone",
                "TAB_TELEGRAM_BOT_TOKEN": "synthetic-bot-token",
                "TAB_AI_PROVIDER_KEY": "synthetic-provider-key",
            }
        ),
    )


async def create_application(
    settings: MongoTestSettings,
    loaded: LoadedConfiguration,
    clock: Clock,
    gateway: Gateway,
) -> tuple[TextIngestionApplication, Foundation]:
    """Build the real runtime composition against one isolated database."""
    config = MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENV),
        database_name=settings.database_name,
        connect_timeout_seconds=5,
    )
    client = create_mongodb_client(config, ResolvedSecrets({_URI_ENV: settings.uri}))
    await verify_mongodb_connection(client, timeout_seconds=5)
    posts = get_posts_collection(client, config)
    await initialize_post_indexes(posts, timeout_seconds=5)
    foundation = Foundation(
        loaded,
        client,
        MongoPostRepository(posts, 5),
        Logger(),
    )
    application = TextIngestionApplication(
        TextIngestionDependencies(
            foundation=cast("FoundationLifecycle", foundation),
            gateway_factory=lambda _loaded: cast("TextIngestionGateway", gateway),
            clock=clock,
            post_id_factory=lambda identity: PostId(
                f"post-{identity.source_channel_id}-{identity.source_message_id}"
            ),
            sleeper=lambda _delay: asyncio.sleep(0),
            jitter_source=lambda: 0.5,
            runtime_ingestor_factory=ingestion_module._create_runtime_ingestor,
        )
    )
    return application, foundation


async def wait_for_count(
    collection: AsyncCollection[MongoDocument],
    query: dict[str, object],
    count: int,
) -> None:
    """Wait briefly for the already-subscribed live prefix to be consumed."""
    for _ in range(200):
        if await collection.count_documents(query) == count:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("Runtime did not persist the expected bounded result.")


def test_history_live_album_overlap_restart_and_graceful_shutdown(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
        loaded = loaded_configuration(tmp_path, mongodb_test_settings.database_name)
        photo = source_message(
            1,
            now - timedelta(seconds=20),
            media_type=MediaType.PHOTO,
            caption="کپشن‌عکس\n✨",
        )
        text = source_message(2, now - timedelta(seconds=19))
        album = tuple(
            source_message(
                identifier,
                published_at,
                media_type=MediaType.PHOTO,
                group_id="album-1",
                caption=f"عضو {identifier}",
            )
            for identifier, published_at in (
                (5, now - timedelta(seconds=16)),
                (3, now - timedelta(seconds=18)),
                (4, now - timedelta(seconds=17)),
            )
        )
        video = source_message(
            6,
            now - timedelta(seconds=1),
            media_type=MediaType.VIDEO,
            caption="ویدئو 😀",
        )
        first_source = MediaSource()
        first_gateway = Gateway((photo, text, *album), [photo, video], first_source)
        first, first_foundation = await create_application(
            mongodb_test_settings, loaded, Clock(now), first_gateway
        )
        await first.start(Path("synthetic.json"), environ={})
        database = first_foundation.client[mongodb_test_settings.database_name]
        await wait_for_count(
            database["content_preparations"],
            {"ready_at": {"$exists": True}},
            4,
        )

        assert await database["posts"].count_documents({}) == 6
        assert await database["media_items"].count_documents({}) == 5
        assert await database["media_groups"].count_documents({}) == 1
        assert await database["content_preparations"].count_documents({}) == 4
        group = await database["media_groups"].find_one({})
        assert group is not None
        members = cast("list[dict[str, object]]", group["members"])
        assert [
            item["source_message_id"]
            for item in sorted(
                members,
                key=lambda item: (
                    cast("datetime", item["source_date"]),
                    cast("int", item["source_message_id"]),
                ),
            )
        ] == [3, 4, 5]
        assert first_source.opens == 5
        await first.shutdown()
        await first.shutdown()
        assert first_gateway.close_calls == 1
        assert first_gateway.subscription is not None
        assert first_gateway.subscription.close_calls == 1
        assert first_foundation.shutdown_calls == 1

        second_source = MediaSource()
        second_gateway = Gateway((photo, text, *album), [photo, video], second_source)
        second, second_foundation = await create_application(
            mongodb_test_settings,
            loaded,
            Clock(now + timedelta(minutes=1)),
            second_gateway,
        )
        await second.start(Path("synthetic.json"), environ={})
        await asyncio.sleep(0.05)
        second_database = second_foundation.client[mongodb_test_settings.database_name]
        assert await second_database["posts"].count_documents({}) == 6
        assert await second_database["media_items"].count_documents({}) == 5
        assert await second_database["content_preparations"].count_documents({}) == 4
        assert second_source.opens == 0
        assert not tuple((tmp_path / "media" / ".tmp").glob("*.partial"))
        await second.shutdown()

    asyncio.run(scenario())


def test_interrupted_download_cleans_partial_and_restart_recovers(
    mongodb_test_settings: MongoTestSettings,
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 7, 13, 8, 0, tzinfo=UTC)
        loaded = loaded_configuration(tmp_path, mongodb_test_settings.database_name)
        photo = source_message(
            10,
            now - timedelta(seconds=1),
            media_type=MediaType.PHOTO,
            caption="بازیابی‌رسانه",
        )
        failing_source = FailingMediaSource()
        failing_gateway = Gateway((photo,), [], failing_source)
        failed, failed_foundation = await create_application(
            mongodb_test_settings, loaded, Clock(now), failing_gateway
        )
        with pytest.raises(TextIngestionStartupError):
            await failed.start(Path("synthetic.json"), environ={})
        assert failing_source.opens == loaded.settings.media.download_max_attempts
        assert not tuple((tmp_path / "media" / ".tmp").glob("*.partial"))
        assert failing_gateway.close_calls == 1
        assert failed_foundation.shutdown_calls == 1

        recovered_source = MediaSource()
        recovered_gateway = Gateway((photo,), [], recovered_source)
        recovered, recovered_foundation = await create_application(
            mongodb_test_settings,
            loaded,
            Clock(now + timedelta(seconds=2)),
            recovered_gateway,
        )
        await recovered.start(Path("synthetic.json"), environ={})
        database = recovered_foundation.client[mongodb_test_settings.database_name]
        assert await database["posts"].count_documents({}) == 1
        assert await database["media_items"].count_documents({}) == 1
        assert (
            await database["content_preparations"].count_documents(
                {"ready_at": {"$exists": True}}
            )
            == 1
        )
        assert recovered_source.opens == 1
        assert len(tuple((tmp_path / "media" / "sha256").rglob("*"))) >= 2
        await recovered.shutdown()

    asyncio.run(scenario())
