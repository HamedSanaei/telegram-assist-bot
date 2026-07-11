from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

import telegram_assist_bot.bootstrap.text_ingestion as ingestion_module
from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    InsertPostResult,
    PostClaimOutcome,
    PostClaimRequest,
    PostClaimResult,
    ResolvedTelegramChannel,
    TelegramAccount,
    TelegramChannelReference,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramTextMessage,
)
from telegram_assist_bot.bootstrap.runtime import FoundationConfigurationError
from telegram_assist_bot.bootstrap.text_ingestion import (
    SystemClock,
    TextIngestionApplication,
    TextIngestionDependencies,
    TextIngestionStartupError,
    create_text_ingestion_application,
    run_text_ingestion_application,
)
from telegram_assist_bot.domain.posts import Post, PostId, SourceMessageIdentity
from telegram_assist_bot.shared.config import (
    ApplicationConfig,
    LoadedConfiguration,
    LogLevel,
    ResolvedSecrets,
)
from telegram_assist_bot.shared.observability import (
    Redactor,
    StructuredLogger,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine, Mapping

    from telegram_assist_bot.application.ports import PostTransitionRequest
    from telegram_assist_bot.infrastructure.telegram.user import (
        TelethonTextIngestionGateway,
    )
    from telegram_assist_bot.shared.observability import RedactedValue


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass(frozen=True)
class Clock:
    now: datetime = datetime(2099, 3, 20, 8, 0, tzinfo=UTC)

    def utc_now(self) -> datetime:
        return self.now


@dataclass
class Repository:
    posts: dict[SourceMessageIdentity, Post] = field(default_factory=dict)
    claims: set[PostId] = field(default_factory=set)

    async def insert_idempotently(self, post: Post) -> InsertPostResult:
        existing = self.posts.get(post.source_identity)
        if existing is not None:
            return InsertPostResult(InsertPostOutcome.ALREADY_EXISTS, existing.post_id)
        self.posts[post.source_identity] = post
        return InsertPostResult(InsertPostOutcome.CREATED, post.post_id)

    async def claim_for_next_stage(self, request: PostClaimRequest) -> PostClaimResult:
        if request.post_id in self.claims:
            return PostClaimResult(PostClaimOutcome.ALREADY_CLAIMED, request.post_id)
        self.claims.add(request.post_id)
        return PostClaimResult(PostClaimOutcome.CLAIMED, request.post_id)

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        del as_of
        return next(
            (post for post in self.posts.values() if post.post_id == post_id), None
        )

    async def get_by_source_identity(
        self,
        source_identity: SourceMessageIdentity,
        *,
        as_of: datetime,
    ) -> Post | None:
        del as_of
        return self.posts.get(source_identity)

    async def list_unexpired(self, *, as_of: datetime, limit: int) -> tuple[Post, ...]:
        del as_of
        return tuple(self.posts.values())[:limit]

    async def transition(self, request: PostTransitionRequest) -> Post:
        return request.post


@dataclass
class Sink:
    events: list[Mapping[str, RedactedValue]] = field(default_factory=list)

    def __call__(self, event: Mapping[str, RedactedValue]) -> None:
        self.events.append(event)


@dataclass
class Foundation:
    loaded: LoadedConfiguration
    repository_value: Repository
    logger_value: StructuredLogger
    order: list[str]
    shutdown_calls: int = 0

    @property
    def repository(self) -> Repository:
        return self.repository_value

    @property
    def logger(self) -> StructuredLogger:
        return self.logger_value

    @property
    def configuration(self) -> LoadedConfiguration:
        return self.loaded

    @property
    def correlation_id(self) -> str:
        return "milestone-one-correlation"

    async def start(
        self,
        configuration_path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> object:
        del configuration_path, environ
        self.order.append("foundation_start")
        return self

    async def shutdown(self) -> None:
        if self.shutdown_calls == 0:
            self.order.append("foundation_shutdown")
        self.shutdown_calls += 1


@dataclass
class Subscription:
    items: list[TelegramTextMessage]
    order: list[str]
    close_calls: int = 0

    def __aiter__(self) -> Subscription:
        return self

    async def __anext__(self) -> TelegramTextMessage:
        if not self.items:
            raise StopAsyncIteration
        self.order.append("live_consume")
        return self.items.pop(0)

    async def close(self) -> None:
        if self.close_calls == 0:
            self.order.append("subscription_close")
        self.close_calls += 1


@dataclass
class Gateway:
    history: tuple[TelegramTextMessage, ...]
    live: list[TelegramTextMessage]
    order: list[str]
    fail_history: bool = False
    registered: dict[int, ResolvedTelegramChannel] = field(default_factory=dict)
    subscription: Subscription | None = None
    close_calls: int = 0

    async def validate_account(self) -> TelegramAccount:
        self.order.append("validate_account")
        return TelegramAccount(42, True)

    async def resolve_channel(
        self,
        reference: TelegramChannelReference,
    ) -> ResolvedTelegramChannel:
        self.order.append(f"resolve_{reference.role.value}")
        return ResolvedTelegramChannel(
            reference.configured_channel_id,
            reference.configured_username,
            reference.config_name,
            True,
            True,
        )

    def register_channel(self, channel: ResolvedTelegramChannel) -> None:
        self.registered[channel.channel_id] = channel

    async def open(self) -> None:
        self.order.append("telegram_open")

    async def subscribe(
        self, source_channel_id: int, *, buffer_size: int
    ) -> Subscription:
        del source_channel_id, buffer_size
        self.order.append("subscribe")
        self.subscription = Subscription(list(self.live), self.order)
        return self.subscription

    async def iter_history_pages(
        self,
        query: TelegramHistoryQuery,
    ) -> AsyncIterator[TelegramHistoryPage]:
        del query
        self.order.append("history")
        if self.fail_history:
            raise RuntimeError("synthetic history failure")
        yield TelegramHistoryPage(self.history)

    async def close(self) -> None:
        if self.close_calls == 0:
            self.order.append("telegram_close")
        self.close_calls += 1


def loaded_configuration() -> LoadedConfiguration:
    root = Path(__file__).resolve().parents[2]
    settings = ApplicationConfig.model_validate_json(
        (root / "config" / "configuration.example.json").read_text(encoding="utf-8")
    )
    return LoadedConfiguration(
        settings,
        ResolvedSecrets(
            {
                "TAB_MONGODB_URI": "mongodb://database.example.invalid:27017",
                "TAB_TELEGRAM_API_ID": "123456",
                "TAB_TELEGRAM_API_HASH": "synthetic-api-hash",
                "TAB_TELEGRAM_PHONE_NUMBER": "synthetic-phone",
                "TAB_TELEGRAM_BOT_TOKEN": "synthetic-bot-value",
                "TAB_AI_PROVIDER_KEY": "synthetic-provider-value",
            }
        ),
    )


def source_message(message_id: int) -> TelegramTextMessage:
    return TelegramTextMessage(
        source_channel_id=-1000000000101,
        source_channel_username="source_example",
        source_channel_display_name="منبع فارسی 📣",
        source_message_id=message_id,
        text="پیام‌پایدار\n😀",
        caption=None,
        text_entities=(),
        caption_entities=(),
        source_published_at=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        is_service=False,
        has_media=False,
    )


def application(gateway: Gateway, foundation: Foundation) -> TextIngestionApplication:
    return TextIngestionApplication(
        TextIngestionDependencies(
            foundation=foundation,
            gateway_factory=lambda _loaded: gateway,
            clock=Clock(),
            post_id_factory=lambda identity: PostId(
                f"post-{identity.source_channel_id}-{identity.source_message_id}"
            ),
            sleeper=lambda _delay: asyncio.sleep(0),
            jitter_source=lambda: 0.5,
        )
    )


def setup() -> tuple[Foundation, Sink, list[str]]:
    order: list[str] = []
    sink = Sink()
    logger = StructuredLogger(
        sink=sink,
        clock=Clock().utc_now,
        redactor=Redactor(),
        minimum_level=LogLevel.DEBUG,
    )
    foundation = Foundation(loaded_configuration(), Repository(), logger, order)
    return foundation, sink, order


def test_subscribes_before_crawl_then_consumes_overlap_idempotently() -> None:
    async def scenario() -> None:
        foundation, sink, order = setup()
        gateway = Gateway(
            history=(source_message(1),),
            live=[source_message(1), source_message(2)],
            order=order,
        )
        app = application(gateway, foundation)

        await app.start(Path("synthetic.json"), environ={})
        await app.wait()
        await app.shutdown()
        await app.shutdown()

        assert order.index("subscribe") < order.index("history")
        assert order.index("history") < order.index("live_consume")
        assert len(foundation.repository.posts) == 2
        assert len(foundation.repository.claims) == 2
        assert foundation.shutdown_calls == 1
        assert gateway.close_calls == 1
        assert gateway.subscription is not None
        assert gateway.subscription.close_calls >= 1
        assert any(
            event["event_name"] == "text_ingestion_ready" for event in sink.events
        )

    run(scenario())


def test_partial_startup_failure_cleans_reverse_owned_resources() -> None:
    async def scenario() -> None:
        foundation, _, order = setup()
        gateway = Gateway((), [], order, fail_history=True)
        app = application(gateway, foundation)

        with pytest.raises(TextIngestionStartupError):
            await app.start(Path("synthetic.json"), environ={})

        assert app.is_ready is False
        assert gateway.subscription is not None
        assert gateway.subscription.close_calls == 1
        assert gateway.close_calls == 1
        assert foundation.shutdown_calls == 1
        assert order[-3:] == [
            "subscription_close",
            "telegram_close",
            "foundation_shutdown",
        ]

    run(scenario())


def test_cancellation_during_crawl_propagates_after_cleanup() -> None:
    @dataclass
    class BlockingGateway(Gateway):
        entered: asyncio.Event | None = None

        async def iter_history_pages(
            self,
            query: TelegramHistoryQuery,
        ) -> AsyncIterator[TelegramHistoryPage]:
            del query
            assert self.entered is not None
            self.entered.set()
            await asyncio.Event().wait()
            yield TelegramHistoryPage(())

    async def scenario() -> None:
        foundation, _, order = setup()
        entered = asyncio.Event()
        gateway = BlockingGateway((), [], order, entered=entered)
        app = application(gateway, foundation)
        task = asyncio.create_task(app.start(Path("synthetic.json"), environ={}))
        await entered.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

        assert gateway.close_calls == 1
        assert foundation.shutdown_calls == 1
        assert app.is_ready is False

    run(scenario())


@dataclass
class RunApplication:
    failure: BaseException | None = None
    starts: int = 0
    waits: int = 0
    shutdowns: int = 0

    async def start(self, path: Path, *, environ: Mapping[str, str]) -> None:
        del path, environ
        self.starts += 1
        if self.failure is not None:
            raise self.failure

    async def wait(self) -> None:
        self.waits += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1


def test_run_wrapper_returns_success_and_always_shuts_down() -> None:
    fake = RunApplication()

    result = run(
        run_text_ingestion_application(
            cast("TextIngestionApplication", fake),
            Path("synthetic.json"),
            environ={},
        )
    )

    assert result.value == 0
    assert (fake.starts, fake.waits, fake.shutdowns) == (1, 1, 1)


def test_run_wrapper_maps_safe_startup_failure() -> None:
    fake = RunApplication(failure=TextIngestionStartupError())

    result = run(
        run_text_ingestion_application(
            cast("TextIngestionApplication", fake),
            Path("synthetic.json"),
            environ={},
        )
    )

    assert result.value == 3
    assert fake.shutdowns == 1


def test_run_wrapper_preserves_foundation_configuration_exit_code() -> None:
    failure = TextIngestionStartupError()
    failure.__cause__ = FoundationConfigurationError()
    fake = RunApplication(failure=failure)

    result = run(
        run_text_ingestion_application(
            cast("TextIngestionApplication", fake),
            Path("synthetic.json"),
            environ={},
        )
    )

    assert result.value == 2
    assert fake.shutdowns == 1


def test_run_wrapper_propagates_cancellation_after_shutdown() -> None:
    fake = RunApplication(failure=asyncio.CancelledError())

    with pytest.raises(asyncio.CancelledError):
        run(
            run_text_ingestion_application(
                cast("TextIngestionApplication", fake),
                Path("synthetic.json"),
                environ={},
            )
        )

    assert fake.shutdowns == 1


def test_concrete_factory_and_gateway_factory_are_inert() -> None:
    sink = Sink()

    app = create_text_ingestion_application(sink=sink, clock=Clock())
    gateway = cast(
        "TelethonTextIngestionGateway",
        ingestion_module._create_gateway(loaded_configuration()),
    )

    assert app.is_ready is False
    assert gateway.session.api_id == 123456
    assert gateway.session.timeout_seconds == 10
    assert SystemClock().utc_now().tzinfo is UTC
