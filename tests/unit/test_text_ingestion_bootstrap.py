from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

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
from telegram_assist_bot.application.validate_telegram_session import (
    TelegramChannelValidationError,
    TelegramChannelValidationIssue,
)
from telegram_assist_bot.bootstrap.runtime import (
    FoundationConfigurationError,
    FoundationExitCode,
)
from telegram_assist_bot.bootstrap.text_ingestion import (
    OperationalRuntimeError,
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
    CorrelationContext,
    Redactor,
    StructuredLogger,
    bind_log_context,
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
    shutdown_reasons: list[str] = field(default_factory=list)

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

    async def shutdown(self, *, reason: str = "requested") -> None:
        if self.shutdown_calls == 0:
            self.order.append("foundation_shutdown")
        self.shutdown_calls += 1
        self.shutdown_reasons.append(reason)


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
            reference.configured_channel_id or -1000000000101,
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
        assert foundation.shutdown_reasons == ["requested"]
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

        with (
            bind_log_context(CorrelationContext(correlation_id="diagnostic-test")),
            pytest.raises(TextIngestionStartupError),
        ):
            await app.start(Path("synthetic.json"), environ={})

        assert app.is_ready is False
        assert gateway.subscription is not None
        assert gateway.subscription.close_calls == 1
        assert gateway.close_calls == 1
        assert foundation.shutdown_calls == 1
        assert foundation.shutdown_reasons == ["startup_failed"]
        assert order[-3:] == [
            "subscription_close",
            "telegram_close",
            "foundation_shutdown",
        ]

    run(scenario())


def test_validation_failure_emits_only_safe_channel_diagnostics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def reject(*_args: object, **_kwargs: object) -> object:
        raise TelegramChannelValidationError(
            (
                TelegramChannelValidationIssue(
                    "source_channels.0",
                    "source_read_denied",
                    "permission",
                ),
            )
        )

    monkeypatch.setattr(ingestion_module, "validate_telegram_startup", reject)

    async def scenario() -> None:
        foundation, sink, order = setup()
        app = application(Gateway((), [], order), foundation)
        emitted: list[dict[str, object]] = []

        class CapturingLogger:
            def emit(self, **kwargs: object) -> None:
                emitted.append(dict(kwargs))

        foundation.logger_value = cast("StructuredLogger", CapturingLogger())

        with (
            bind_log_context(CorrelationContext(correlation_id="diagnostic-test")),
            pytest.raises(TextIngestionStartupError),
        ):
            await app.start(Path("synthetic.json"), environ={})

        del sink
        matching = [
            item
            for item in emitted
            if item["event_name"] == "telegram_validation_failed"
        ]
        assert matching
        event = matching[0]
        fields = event["fields"]
        assert fields == {
            "configuration_path": "source_channels.0",
            "issue_code": "source_read_denied",
            "failure_category": "permission",
            "channel_role": "source",
        }

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
class BlockingSubscription(Subscription):
    entered: asyncio.Event | None = None

    async def __anext__(self) -> TelegramTextMessage:
        assert self.entered is not None
        self.order.append("live_started")
        self.entered.set()
        await asyncio.Event().wait()
        raise StopAsyncIteration


@dataclass
class RuntimeGateway(Gateway):
    history_entered: asyncio.Event | None = None
    history_release: asyncio.Event | None = None
    live_entered: asyncio.Event | None = None
    second_history_attempt: asyncio.Event | None = None
    fail_first_history: bool = False
    history_attempts: int = 0
    disconnected: asyncio.Event | None = None

    async def subscribe(
        self, source_channel_id: int, *, buffer_size: int
    ) -> BlockingSubscription:
        del source_channel_id, buffer_size
        self.order.append("subscribe")
        self.subscription = BlockingSubscription(
            [], self.order, entered=self.live_entered
        )
        return self.subscription

    async def wait_disconnected(self) -> None:
        if self.disconnected is None:
            self.disconnected = asyncio.Event()
        await self.disconnected.wait()

    async def iter_history_pages(
        self,
        query: TelegramHistoryQuery,
    ) -> AsyncIterator[TelegramHistoryPage]:
        del query
        self.history_attempts += 1
        if self.history_attempts >= 2 and self.second_history_attempt is not None:
            self.second_history_attempt.set()
        self.order.append("history_started")
        assert self.history_entered is not None
        self.history_entered.set()
        if self.fail_first_history and self.history_attempts == 1:
            raise RuntimeError("synthetic history failure")
        assert self.history_release is not None
        await self.history_release.wait()
        yield TelegramHistoryPage(())


class CriticalRuntimeWorker:
    def __init__(self, order: list[str]) -> None:
        self.order = order
        self.ready = asyncio.Event()
        self.fail = asyncio.Event()
        self.heartbeat_count = 0
        self.publication_polls = 0
        self.immediate_job = asyncio.Event()
        self.immediate_published = asyncio.Event()
        self.publication_count = 0
        self.stopped = asyncio.Event()
        self.second_heartbeat = asyncio.Event()

    async def wait_ready(self) -> None:
        await self.ready.wait()

    async def run(self) -> None:
        self.order.extend(("heartbeat_active", "publication_started"))
        self.heartbeat_count = 1
        self.publication_polls = 1
        self.ready.set()
        try:
            while not self.fail.is_set():
                await asyncio.sleep(0.01)
                self.heartbeat_count += 1
                self.publication_polls += 1
                if (
                    self.immediate_job.is_set()
                    and not self.immediate_published.is_set()
                ):
                    self.publication_count += 1
                    self.immediate_published.set()
                if self.heartbeat_count >= 2:
                    self.second_heartbeat.set()
            raise RuntimeError("synthetic critical failure")
        finally:
            self.order.append("runtime_stopped")
            self.stopped.set()


class ReturningRuntimeWorker(CriticalRuntimeWorker):
    def __init__(self, order: list[str]) -> None:
        super().__init__(order)
        self.return_now = asyncio.Event()

    async def run(self) -> None:
        self.order.extend(("heartbeat_active", "publication_started"))
        self.ready.set()
        await self.return_now.wait()


def runtime_application(
    gateway: RuntimeGateway,
    foundation: Foundation,
    worker: CriticalRuntimeWorker,
    *,
    sleeper: object | None = None,
    runtime_ingestor_factory: object | None = None,
) -> TextIngestionApplication:
    async def worker_factory(*_args: object) -> CriticalRuntimeWorker:
        return worker

    return TextIngestionApplication(
        TextIngestionDependencies(
            foundation=foundation,
            gateway_factory=lambda _loaded: gateway,
            clock=Clock(),
            post_id_factory=lambda identity: PostId(
                f"post-{identity.source_channel_id}-{identity.source_message_id}"
            ),
            sleeper=cast("Any", sleeper or (lambda _delay: asyncio.sleep(0))),
            jitter_source=lambda: 0.5,
            runtime_ingestor_factory=cast("Any", runtime_ingestor_factory),
            runtime_worker_factory=worker_factory,
        )
    )


def test_operational_services_and_live_start_before_blocked_history() -> None:
    async def scenario() -> None:
        foundation, sink, order = setup()
        history_entered = asyncio.Event()
        live_entered = asyncio.Event()
        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=history_entered,
            history_release=asyncio.Event(),
            live_entered=live_entered,
        )
        worker = CriticalRuntimeWorker(order)
        app = runtime_application(gateway, foundation, worker)

        await app.start(Path("synthetic.json"), environ={})
        await asyncio.wait_for(history_entered.wait(), timeout=1)
        await asyncio.wait_for(live_entered.wait(), timeout=1)
        worker.immediate_job.set()
        await asyncio.wait_for(worker.immediate_published.wait(), timeout=1)

        assert app.is_ready
        assert worker.heartbeat_count >= 1
        assert worker.publication_polls >= 1
        assert worker.publication_count == 1
        assert order.index("heartbeat_active") < order.index("history_started")
        assert order.index("publication_started") < order.index("history_started")
        assert order.index("live_started") < order.index("history_started")
        names = [event["event_name"] for event in sink.events]
        assert names.index("operational_runtime_starting") < names.index(
            "operational_runtime_ready"
        )
        assert names.index("operational_runtime_ready") < names.index(
            "history_crawl_started"
        )

        await app.shutdown()
        assert worker.stopped.is_set()
        assert order.index("runtime_stopped") < order.index("telegram_close")
        assert order.index("telegram_close") < order.index("foundation_shutdown")

    run(scenario())


def test_history_failure_retries_without_stopping_critical_services() -> None:
    async def scenario() -> None:
        foundation, _, order = setup()
        retry_started = asyncio.Event()

        async def sleeper(_delay: float) -> None:
            retry_started.set()
            await asyncio.sleep(0)

        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=asyncio.Event(),
            history_release=asyncio.Event(),
            live_entered=asyncio.Event(),
            second_history_attempt=asyncio.Event(),
            fail_first_history=True,
        )
        worker = CriticalRuntimeWorker(order)
        app = runtime_application(gateway, foundation, worker, sleeper=sleeper)

        await app.start(Path("synthetic.json"), environ={})
        await asyncio.wait_for(retry_started.wait(), timeout=1)
        assert gateway.second_history_attempt is not None
        await asyncio.wait_for(gateway.second_history_attempt.wait(), timeout=1)
        await asyncio.wait_for(worker.second_heartbeat.wait(), timeout=1)

        assert app.is_ready
        assert gateway.history_attempts >= 2
        assert worker.publication_polls >= 2
        await app.shutdown()

    run(scenario())


def test_completed_history_and_listener_registration_do_not_stop_runtime() -> None:
    async def scenario() -> None:
        foundation, sink, order = setup()
        history_release = asyncio.Event()
        history_release.set()
        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=asyncio.Event(),
            history_release=history_release,
            live_entered=asyncio.Event(),
        )
        worker = CriticalRuntimeWorker(order)
        app = runtime_application(gateway, foundation, worker)
        loop = asyncio.get_running_loop()
        unobserved: list[dict[str, object]] = []
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: unobserved.append(context))
        try:
            await app.start(Path("synthetic.json"), environ={})
            assert gateway.history_entered is not None
            await asyncio.wait_for(gateway.history_entered.wait(), timeout=1)
            await asyncio.wait_for(worker.second_heartbeat.wait(), timeout=1)
            waiter = asyncio.create_task(app.wait())
            await asyncio.sleep(0.03)

            names = [event["event_name"] for event in sink.events]
            assert names.index("operational_runtime_ready") < names.index(
                "history_crawl_started"
            )
            assert "history_crawl_completed" in names
            assert "runtime_task_completed_unexpectedly" not in names
            assert waiter.done() is False
            assert foundation.shutdown_calls == 0
            assert worker.publication_polls >= 2

            app.request_stop()
            await asyncio.wait_for(waiter, timeout=1)
            await app.shutdown()
            await asyncio.sleep(0)
            assert foundation.shutdown_reasons == ["requested"]
            assert unobserved == []
        finally:
            loop.set_exception_handler(previous_handler)

    run(scenario())


def test_unexpected_telethon_disconnect_stops_runtime_with_safe_reason() -> None:
    async def scenario() -> None:
        foundation, sink, order = setup()
        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=asyncio.Event(),
            history_release=asyncio.Event(),
            live_entered=asyncio.Event(),
            disconnected=asyncio.Event(),
        )
        worker = CriticalRuntimeWorker(order)
        app = runtime_application(gateway, foundation, worker)
        await app.start(Path("synthetic.json"), environ={})
        assert gateway.disconnected is not None
        gateway.disconnected.set()

        with pytest.raises(OperationalRuntimeError) as captured:
            await app.wait()
        await app.shutdown()

        event = next(
            event
            for event in sink.events
            if event["event_name"] == "runtime_task_completed_unexpectedly"
        )
        assert event["task_name"] == "telethon-disconnected"
        assert event["completion_kind"] == "returned"
        assert "failure_type" not in event
        assert isinstance(captured.value.__cause__, RuntimeError)
        assert foundation.shutdown_reasons == ["telethon_disconnected"]

    run(scenario())


def test_normally_returned_critical_worker_is_an_infrastructure_failure() -> None:
    async def scenario() -> None:
        foundation, sink, order = setup()
        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=asyncio.Event(),
            history_release=asyncio.Event(),
            live_entered=asyncio.Event(),
        )
        worker = ReturningRuntimeWorker(order)
        app = runtime_application(gateway, foundation, worker)
        await app.start(Path("synthetic.json"), environ={})
        worker.return_now.set()

        with pytest.raises(OperationalRuntimeError):
            await app.wait()
        await app.shutdown()

        event = next(
            event
            for event in sink.events
            if event["event_name"] == "runtime_task_completed_unexpectedly"
        )
        assert event["task_name"] == "operational-publication"
        assert event["completion_kind"] == "returned"
        assert "failure_type" not in event
        assert foundation.shutdown_reasons == ["critical_task_failed"]

    run(scenario())


def test_album_finalizer_failure_uses_real_logger_and_reports_exact_task() -> None:
    class FailingRuntimeIngestor:
        async def finalize_due_groups(self) -> None:
            raise ValueError("synthetic private provider detail")

    async def ingestor_factory(*_args: object) -> FailingRuntimeIngestor:
        return FailingRuntimeIngestor()

    async def scenario() -> None:
        foundation, sink, order = setup()
        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=asyncio.Event(),
            history_release=asyncio.Event(),
            live_entered=asyncio.Event(),
        )
        worker = CriticalRuntimeWorker(order)
        app = runtime_application(
            gateway,
            foundation,
            worker,
            runtime_ingestor_factory=ingestor_factory,
        )
        await app.start(Path("synthetic.json"), environ={})

        with pytest.raises(OperationalRuntimeError) as captured:
            await app.wait()
        await app.shutdown()

        content_event = next(
            event
            for event in sink.events
            if event["event_name"] == "content_preparation_failed"
        )
        assert content_event["failure_category"] == "permanent"
        assert content_event["failure_type"] == "ValueError"
        task_event = next(
            event
            for event in sink.events
            if event["event_name"] == "runtime_task_completed_unexpectedly"
        )
        assert task_event["task_name"] == "telegram-album-finalizer"
        assert task_event["completion_kind"] == "failed"
        assert task_event["failure_type"] == "TextIngestionStartupError"
        assert isinstance(captured.value.__cause__, TextIngestionStartupError)
        assert isinstance(captured.value.__cause__.__cause__, ValueError)
        rendered = repr(sink.events)
        assert "synthetic private provider detail" not in rendered

    run(scenario())


def test_critical_runtime_failure_propagates_and_drains_every_task() -> None:
    async def scenario() -> None:
        foundation, sink, order = setup()
        gateway = RuntimeGateway(
            (),
            [],
            order,
            history_entered=asyncio.Event(),
            history_release=asyncio.Event(),
            live_entered=asyncio.Event(),
        )
        worker = CriticalRuntimeWorker(order)
        app = runtime_application(gateway, foundation, worker)
        await app.start(Path("synthetic.json"), environ={})
        worker.fail.set()

        with pytest.raises(OperationalRuntimeError) as captured:
            await app.wait()
        tasks = tuple(app._listener_tasks)
        await app.shutdown()

        assert worker.stopped.is_set()
        assert gateway.close_calls == 1
        assert foundation.shutdown_calls == 1
        assert tasks
        assert all(task.done() for task in tasks)
        assert isinstance(captured.value.__cause__, RuntimeError)
        event = next(
            event
            for event in sink.events
            if event["event_name"] == "runtime_task_completed_unexpectedly"
        )
        assert event["task_name"] == "operational-publication"
        assert event["completion_kind"] == "failed"
        assert event["failure_type"] == "RuntimeError"
        assert foundation.shutdown_reasons == ["critical_task_failed"]

    run(scenario())


@dataclass
class RunApplication:
    failure: BaseException | None = None
    wait_failure: BaseException | None = None
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
        if self.wait_failure is not None:
            raise self.wait_failure

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


def test_run_wrapper_maps_runtime_failure_and_still_shuts_down() -> None:
    fake = RunApplication(wait_failure=RuntimeError("synthetic provider detail"))

    result = run(
        run_text_ingestion_application(
            cast("TextIngestionApplication", fake),
            Path("synthetic.json"),
            environ={},
        )
    )

    assert result is FoundationExitCode.INFRASTRUCTURE_ERROR
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
