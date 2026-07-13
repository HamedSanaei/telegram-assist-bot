"""Composition root and lifecycle for Milestone 1 text ingestion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self, cast
from uuid import uuid4

from telegram_assist_bot.application import (
    CrawlTodayResult,
    CrawlTodayTextPosts,
    HandleLiveMessage,
    IngestPostIdempotently,
    TelegramValidationReport,
    TextMessageIngestor,
)
from telegram_assist_bot.application.assemble_media_group import AssembleMediaGroup
from telegram_assist_bot.application.categorize_post import KeywordCategoryRule
from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.native_scheduling import RunNativeScheduling
from telegram_assist_bot.application.ports import (
    Clock,
    MediaSource,
    NativeScheduleCommand,
    PostRepository,
    ResolvedTelegramChannel,
    TelegramHistoryGateway,
    TelegramLiveGateway,
    TelegramLiveSubscription,
    TelegramPublisherGateway,
    TelegramValidationGateway,
)
from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparePostPipeline,
    validate_unimplemented_ai_flags,
)
from telegram_assist_bot.application.publication import (
    PublishImmediately,
    PublishRequest,
)
from telegram_assist_bot.application.runtime_ingestion import (
    RuntimeMessageIngestor,
    RuntimePreparationPolicy,
    RuntimeSourcePolicy,
)
from telegram_assist_bot.application.scheduling import RunDuePublication, RunDueStatus
from telegram_assist_bot.application.validate_telegram_session import (
    TelegramChannelValidationError,
)
from telegram_assist_bot.bootstrap.runtime import (
    FoundationExitCode,
    FoundationStartupError,
    create_foundation_application,
)
from telegram_assist_bot.bootstrap.telegram_validation import validate_telegram_startup
from telegram_assist_bot.domain.categories import Category
from telegram_assist_bot.domain.posts import PostId, SourceMessageIdentity
from telegram_assist_bot.domain.publication import publication_identity
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoNativeScheduleRepository,
    MongoOperationalApprovalRepository,
    MongoPublicationPayloadLoader,
    MongoPublicationRepository,
    MongoRuntimeHeartbeatRepository,
    MongoScheduleRepository,
    initialize_native_schedule_indexes,
    initialize_operational_approval_indexes,
    initialize_publication_indexes,
)
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.infrastructure.telegram.user import (
    TelethonSessionAdapter,
    TelethonTextIngestionGateway,
)
from telegram_assist_bot.shared.config import LoadedConfiguration, LogLevel
from telegram_assist_bot.shared.retry import RetryPolicy
from telegram_assist_bot.workers import LiveTextListener, ScheduledPublicationWorker

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pymongo import AsyncMongoClient

    from telegram_assist_bot.application.ports import TelegramNativeSchedulerGateway
    from telegram_assist_bot.domain import ScheduledPublication
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )
    from telegram_assist_bot.shared.observability import EventSink, StructuredLogger
    from telegram_assist_bot.shared.retry import AsyncSleeper, JitterSource


class TextIngestionStartupError(RuntimeError):
    """Report a safe failure before the text-ingestion lifecycle becomes ready."""

    error_category = "permanent"

    def __init__(self, *, cause: BaseException | None = None) -> None:
        """Retain a cause without copying provider details into the message."""
        super().__init__("Telegram text ingestion could not be started.")
        if cause is not None:
            self.__cause__ = cause


class OperationalRuntimeError(RuntimeError):
    """Report one safely classified critical runtime termination."""

    error_category = "transient"

    def __init__(self, *, cause: BaseException | None = None) -> None:
        """Retain the original task failure without exposing its message."""
        super().__init__("A critical operational runtime task stopped.")
        if cause is not None:
            self.__cause__ = cause


class _State(IntEnum):
    NEW = 0
    STARTING = 1
    READY = 2
    STOPPING = 3
    STOPPED = 4
    FAILED = 5


class FoundationLifecycle(Protocol):
    """Describe the T006 lifecycle surface consumed by Milestone 1."""

    @property
    def repository(self) -> PostRepository:
        """Return the ready post repository."""
        ...

    @property
    def logger(self) -> StructuredLogger:
        """Return the ready structured logger."""
        ...

    @property
    def configuration(self) -> LoadedConfiguration:
        """Return the immutable loaded configuration."""
        ...

    @property
    def correlation_id(self) -> str | None:
        """Return the foundation correlation identifier."""
        ...

    async def start(
        self,
        configuration_path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> object:
        """Start the foundation before Telegram resources."""
        ...

    async def shutdown(self, *, reason: str = "requested") -> None:
        """Close the foundation idempotently."""
        ...


class TextIngestionGateway(
    TelegramValidationGateway,
    TelegramHistoryGateway,
    TelegramLiveGateway,
    Protocol,
):
    """Combine startup validation and one owned Telegram runtime resource."""

    def register_channel(self, channel: ResolvedTelegramChannel) -> None:
        """Register startup-validated metadata for DTO mapping."""
        ...

    async def open(self) -> None:
        """Open the existing session non-interactively."""
        ...

    async def close(self) -> None:
        """Close the runtime client and release its session lock."""
        ...


class FullFoundationLifecycle(FoundationLifecycle, Protocol):
    """Expose the owned MongoDB client only to concrete runtime composition."""

    @property
    def mongodb_client(self) -> AsyncMongoClient[MongoDocument]:
        """Return the already-owned client while foundation is ready."""
        ...


class FullTextIngestionGateway(TextIngestionGateway, Protocol):
    """Expose media streaming over the same already-open Telegram client."""

    def media_source(self) -> MediaSource:
        """Return a streamer over the already-open owned Telegram client."""
        ...

    def publisher(self, *, media_root: Path) -> TelegramPublisherGateway:
        """Return a publisher over the same already-open User API client."""
        ...

    def native_scheduler(self, *, media_root: Path) -> object:
        """Return native scheduling over the same already-open User API client."""
        ...

    async def wait_disconnected(self) -> None:
        """Wait until the already-open shared client disconnects."""
        ...


class RuntimeWorker(Protocol):
    """Describe one cancellation-safe operational background worker."""

    async def wait_ready(self) -> None:
        """Wait until heartbeat and publication polling are both active."""
        ...

    async def run(self) -> None:
        """Run until cancellation."""
        ...


class SystemClock(Clock):
    """Return current UTC time for concrete composition."""

    def utc_now(self) -> datetime:
        """Return one timezone-aware UTC instant."""
        return datetime.now(UTC)


type GatewayFactory = Callable[[LoadedConfiguration], TextIngestionGateway]
type PostIdFactory = Callable[[SourceMessageIdentity], PostId]
type RuntimeIngestorFactory = Callable[
    [
        LoadedConfiguration,
        TelegramValidationReport,
        TextIngestionGateway,
        TextMessageIngestor,
        FoundationLifecycle,
        Clock,
    ],
    Awaitable[RuntimeMessageIngestor],
]
type RuntimeWorkerFactory = Callable[
    [
        LoadedConfiguration,
        TelegramValidationReport,
        TextIngestionGateway,
        FoundationLifecycle,
    ],
    Awaitable[RuntimeWorker],
]


@dataclass(frozen=True, slots=True)
class TextIngestionDependencies:
    """Hold explicit collaborators for one text-ingestion lifecycle."""

    foundation: FoundationLifecycle = field(repr=False)
    gateway_factory: GatewayFactory = field(repr=False)
    clock: Clock = field(repr=False)
    post_id_factory: PostIdFactory = field(repr=False)
    sleeper: AsyncSleeper = field(repr=False)
    jitter_source: JitterSource = field(repr=False)
    runtime_ingestor_factory: RuntimeIngestorFactory | None = field(
        default=None, repr=False
    )
    runtime_worker_factory: RuntimeWorkerFactory | None = field(
        default=None, repr=False
    )


class TextIngestionApplication:
    """Own validation, subscribe-before-crawl, listener, and reverse cleanup."""

    __slots__ = (
        "_crawl_results",
        "_critical_tasks",
        "_dependencies",
        "_foundation_owned",
        "_gateway",
        "_listener_tasks",
        "_runtime_ingestor",
        "_shutdown_reason",
        "_shutdown_task",
        "_state",
        "_stop_event",
        "_subscriptions",
    )

    def __init__(self, dependencies: TextIngestionDependencies) -> None:
        """Create an inert lifecycle with no import-time or construction I/O."""
        self._dependencies = dependencies
        self._state = _State.NEW
        self._foundation_owned = False
        self._gateway: TextIngestionGateway | None = None
        self._runtime_ingestor: RuntimeMessageIngestor | None = None
        self._subscriptions: list[TelegramLiveSubscription] = []
        self._listener_tasks: list[asyncio.Task[object]] = []
        self._critical_tasks: list[asyncio.Task[object]] = []
        self._crawl_results: dict[int, CrawlTodayResult] = {}
        self._shutdown_task: asyncio.Task[None] | None = None
        self._shutdown_reason: str | None = None
        self._stop_event = asyncio.Event()

    @property
    def is_ready(self) -> bool:
        """Return whether validation, subscriptions, and crawls all succeeded."""
        return self._state is _State.READY

    @property
    def crawl_results(self) -> Mapping[int, CrawlTodayResult]:
        """Return a detached startup crawl result mapping."""
        return dict(self._crawl_results)

    async def start(
        self,
        configuration_path: Path,
        *,
        environ: Mapping[str, str],
    ) -> Self:
        """Start in gap-minimizing order and never prompt for authentication."""
        if self._state is not _State.NEW:
            raise TextIngestionStartupError
        self._state = _State.STARTING
        try:
            await self._dependencies.foundation.start(
                configuration_path,
                environ=environ,
            )
            self._foundation_owned = True
            loaded = self._dependencies.foundation.configuration
            logger = self._dependencies.foundation.logger
            correlation_id = self._dependencies.foundation.correlation_id
            if correlation_id is None:
                raise TextIngestionStartupError
            gateway = self._dependencies.gateway_factory(loaded)
            self._gateway = gateway
            try:
                report = await validate_telegram_startup(loaded.settings, gateway)
            except TelegramChannelValidationError as error:
                for issue in error.issues:
                    role = (
                        "source"
                        if issue.configuration_path.startswith("source_channels.")
                        else "destination"
                    )
                    logger.emit(
                        level=LogLevel.ERROR,
                        event_name="telegram_validation_failed",
                        fields={
                            "configuration_path": issue.configuration_path,
                            "issue_code": issue.code,
                            "failure_category": issue.error_category,
                            "channel_role": role,
                        },
                    )
                raise
            logger.emit(
                level=LogLevel.INFO,
                event_name="telegram_validation_succeeded",
                fields={"channel_count": len(report.channels)},
            )
            for validated in report.channels:
                gateway.register_channel(validated.channel)
            await gateway.open()
            logger.emit(level=LogLevel.INFO, event_name="telegram_session_opened")
            disconnect_task: asyncio.Task[object] | None = None
            if self._dependencies.runtime_worker_factory is not None:
                runtime_gateway = cast("FullTextIngestionGateway", gateway)
                disconnect_task = cast_task(
                    asyncio.create_task(
                        runtime_gateway.wait_disconnected(),
                        name="telethon-disconnected",
                    )
                )
                self._track_task(disconnect_task, critical=True)

            sources = tuple(
                item.channel for item in report.channels if item.role.value == "Source"
            )
            if not sources:
                raise TextIngestionStartupError
            ingestion_config = loaded.settings.telegram.ingestion
            prepared: dict[int, TelegramLiveSubscription] = {}
            for source in sources:
                subscription = await gateway.subscribe(
                    source.channel_id,
                    buffer_size=ingestion_config.live_buffer_size,
                )
                self._subscriptions.append(subscription)
                prepared[source.channel_id] = subscription
            logger.emit(
                level=LogLevel.INFO,
                event_name="telegram_subscriptions_ready",
                fields={"source_count": len(sources)},
            )

            retry_policy = RetryPolicy(
                max_attempts=ingestion_config.max_reconnect_attempts,
                initial_delay_seconds=(
                    ingestion_config.reconnect_initial_delay_seconds
                ),
                max_delay_seconds=ingestion_config.reconnect_max_delay_seconds,
            )
            base_ingestor = IngestPostIdempotently(
                self._dependencies.foundation.repository,
                self._dependencies.clock,
                self._dependencies.post_id_factory,
                logger,
            )
            ingestor: TextMessageIngestor = base_ingestor
            if self._dependencies.runtime_ingestor_factory is not None:
                self._runtime_ingestor = await (
                    self._dependencies.runtime_ingestor_factory(
                        loaded,
                        report,
                        gateway,
                        base_ingestor,
                        self._dependencies.foundation,
                        self._dependencies.clock,
                    )
                )
                ingestor = self._runtime_ingestor

            def crawler() -> CrawlTodayTextPosts:
                return CrawlTodayTextPosts(
                    gateway=gateway,
                    ingestor=ingestor,
                    clock=self._dependencies.clock,
                    timezone=loaded.settings.timezone,
                    retry_policy=retry_policy,
                    logger=logger,
                    sleeper=self._dependencies.sleeper,
                    jitter_source=self._dependencies.jitter_source,
                    page_size=ingestion_config.history_page_size,
                    max_pages=ingestion_config.history_max_pages,
                )

            async def crawl_source(source: ResolvedTelegramChannel) -> None:
                logger.emit(
                    level=LogLevel.INFO,
                    event_name="history_crawl_started",
                    fields={"source_channel_id": source.channel_id},
                )
                result = await crawler().execute(
                    source.channel_id,
                    correlation_id=correlation_id,
                )
                self._crawl_results[source.channel_id] = result
                if self._runtime_ingestor is not None:
                    await self._runtime_ingestor.finalize_due_groups()
                logger.emit(
                    level=LogLevel.INFO,
                    event_name="telegram_history_crawl_completed",
                    fields={
                        "source_channel_id": source.channel_id,
                        "created": result.created,
                        "already_existing": result.already_existing,
                    },
                )

                logger.emit(
                    level=LogLevel.INFO,
                    event_name="history_crawl_completed",
                    fields={
                        "source_channel_id": source.channel_id,
                        "created": result.created,
                        "already_existing": result.already_existing,
                    },
                )

            def start_listener(source: ResolvedTelegramChannel) -> None:
                listener = LiveTextListener(
                    gateway=gateway,
                    handler=HandleLiveMessage(ingestor),
                    retry_policy=retry_policy,
                    logger=logger,
                    sleeper=self._dependencies.sleeper,
                    jitter_source=self._dependencies.jitter_source,
                    buffer_size=ingestion_config.live_buffer_size,
                    maximum_flood_wait_seconds=(
                        ingestion_config.maximum_flood_wait_seconds
                    ),
                )
                task = asyncio.create_task(
                    listener.run(
                        source.channel_id,
                        correlation_id=correlation_id,
                        initial_subscription=prepared[source.channel_id],
                    ),
                    name=f"telegram-live-{source.channel_id}",
                )
                self._track_task(
                    cast_task(task),
                    critical=self._dependencies.runtime_worker_factory is not None,
                )
                self._subscriptions.remove(prepared[source.channel_id])

            async def run_history_crawl() -> None:
                retry_delay = min(
                    30.0,
                    max(
                        0.1,
                        float(ingestion_config.reconnect_initial_delay_seconds),
                    ),
                )
                for source in sources:
                    while True:
                        try:
                            await crawl_source(source)
                        except asyncio.CancelledError:
                            raise
                        except Exception as error:  # noqa: BLE001 - isolated retry loop.
                            logger.emit(
                                level=LogLevel.ERROR,
                                event_name="history_crawl_failed",
                                fields={
                                    "source_channel_id": source.channel_id,
                                    "failure_category": getattr(
                                        error, "error_category", "transient"
                                    ),
                                    "failure_type": type(error).__name__,
                                },
                            )
                            await self._dependencies.sleeper(retry_delay)
                            continue
                        break

            if self._dependencies.runtime_worker_factory is not None:
                logger.emit(
                    level=LogLevel.INFO, event_name="operational_runtime_starting"
                )
                operational_worker = await self._dependencies.runtime_worker_factory(
                    loaded, report, gateway, self._dependencies.foundation
                )
                publication_task = asyncio.create_task(
                    operational_worker.run(), name="operational-publication"
                )
                tracked_publication = cast_task(publication_task)
                self._track_task(tracked_publication, critical=True)
                readiness_task = asyncio.create_task(
                    operational_worker.wait_ready(), name="operational-readiness"
                )
                startup_tasks: tuple[asyncio.Task[object], ...]
                if disconnect_task is None:
                    startup_tasks = (tracked_publication, cast_task(readiness_task))
                else:
                    startup_tasks = (
                        tracked_publication,
                        disconnect_task,
                        cast_task(readiness_task),
                    )
                done, _pending = await asyncio.wait(
                    startup_tasks,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                completed_critical = next(
                    (task for task in self._critical_tasks if task in done), None
                )
                if completed_critical is not None:
                    readiness_task.cancel()
                    await asyncio.gather(readiness_task, return_exceptions=True)
                    await self._raise_unexpected_task(completed_critical)
                await readiness_task
                for source in sources:
                    start_listener(source)
                await asyncio.sleep(0)
                logger.emit(
                    level=LogLevel.INFO,
                    event_name="live_ingestion_started",
                    fields={"source_count": len(sources)},
                )
                if self._runtime_ingestor is not None:
                    album_task = asyncio.create_task(
                        self._run_album_finalizer(), name="telegram-album-finalizer"
                    )
                    self._track_task(cast_task(album_task), critical=True)
                self._state = _State.READY
                logger.emit(level=LogLevel.INFO, event_name="operational_runtime_ready")
                history_task = asyncio.create_task(
                    run_history_crawl(), name="telegram-history-crawl"
                )
                self._track_task(cast_task(history_task), critical=False)
            else:
                for source in sources:
                    await crawl_source(source)
                    start_listener(source)
            if (
                self._runtime_ingestor is not None
                and self._dependencies.runtime_worker_factory is None
            ):
                album_task = asyncio.create_task(
                    self._run_album_finalizer(), name="telegram-album-finalizer"
                )
                self._track_task(cast_task(album_task), critical=False)
            self._state = _State.READY
            logger.emit(
                level=LogLevel.INFO,
                event_name="text_ingestion_ready",
                fields={"source_count": len(sources)},
            )
            logger.emit(
                level=LogLevel.INFO,
                event_name="full_ingestion_ready",
                fields={"source_count": len(sources)},
            )
            return self
        except asyncio.CancelledError:
            self._set_shutdown_reason("requested")
            self._state = _State.FAILED
            await self._cleanup()
            raise
        except Exception as error:
            self._set_shutdown_reason("startup_failed")
            self._state = _State.FAILED
            await self._cleanup()
            if isinstance(error, TextIngestionStartupError):
                raise
            raise TextIngestionStartupError(cause=error) from error

    async def wait(self) -> None:
        """Wait for an explicit stop or one genuinely critical runtime task."""
        if not self.is_ready:
            raise TextIngestionStartupError
        if self._dependencies.runtime_worker_factory is None:
            if self._listener_tasks:
                await asyncio.gather(*self._listener_tasks)
            return
        stop_task = asyncio.create_task(
            self._stop_event.wait(), name="operational-stop-request"
        )
        try:
            done, _pending = await asyncio.wait(
                (stop_task, *self._critical_tasks),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if stop_task in done:
                return
            completed = next(task for task in self._critical_tasks if task in done)
            await self._raise_unexpected_task(completed)
        finally:
            if not stop_task.done():
                stop_task.cancel()
            await asyncio.gather(stop_task, return_exceptions=True)

    def request_stop(self) -> None:
        """Request a normal runtime stop without closing resources inline."""
        self._set_shutdown_reason("requested")
        self._stop_event.set()

    def _track_task(self, task: asyncio.Task[object], *, critical: bool) -> None:
        self._listener_tasks.append(task)
        if critical:
            self._critical_tasks.append(task)

    async def _raise_unexpected_task(self, task: asyncio.Task[object]) -> None:
        fields, original = _unexpected_task_details(task)
        task_name = task.get_name()
        if isinstance(original, OperationalRuntimeError):
            self._shutdown_reason = "critical_task_failed"
            raise original
        self._dependencies.foundation.logger.emit(
            level=LogLevel.ERROR,
            event_name="runtime_task_completed_unexpectedly",
            fields=fields,
        )
        reason = (
            "telethon_disconnected"
            if task_name == "telethon-disconnected"
            else "critical_task_failed"
        )
        self._shutdown_reason = reason
        failure = OperationalRuntimeError(cause=original)
        raise failure from original

    def _set_shutdown_reason(self, reason: str) -> None:
        if self._shutdown_reason is None:
            self._shutdown_reason = reason

    async def shutdown(self) -> None:
        """Cancel listeners and close every owned resource exactly once."""
        self.request_stop()
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._cleanup())
        await self._shutdown_task

    async def _cleanup(self) -> None:
        if self._state is _State.STOPPED:
            return
        self._state = _State.STOPPING
        self._stop_event.set()
        for task in reversed(self._listener_tasks):
            if not task.done():
                task.cancel()
        if self._listener_tasks:
            await asyncio.gather(*self._listener_tasks, return_exceptions=True)
        self._listener_tasks.clear()
        self._critical_tasks.clear()
        for subscription in reversed(self._subscriptions):
            await subscription.close()
        self._subscriptions.clear()
        if self._gateway is not None:
            await self._gateway.close()
            self._gateway = None
        if self._foundation_owned:
            await self._dependencies.foundation.shutdown(
                reason=self._shutdown_reason or "requested"
            )
            self._foundation_owned = False
        self._state = _State.STOPPED

    async def _run_album_finalizer(self) -> None:
        """Poll persisted Album deadlines with one bounded background task."""
        if self._runtime_ingestor is None:
            return
        try:
            while True:
                await self._runtime_ingestor.finalize_due_groups()
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            safe_error = TextIngestionStartupError(cause=error)
            self._dependencies.foundation.logger.emit(
                level=LogLevel.ERROR,
                event_name="content_preparation_failed",
                fields={
                    "failure_category": getattr(error, "error_category", "permanent"),
                    "failure_type": type(error).__name__,
                },
                error=safe_error,
            )
            raise safe_error from error


def cast_task(task: asyncio.Task[object]) -> asyncio.Task[object]:
    """Keep task storage explicit for strict type checking."""
    return task


def _unexpected_task_details(
    task: asyncio.Task[object],
) -> tuple[dict[str, object], BaseException]:
    """Classify one completed task without exposing exception details."""
    if task.cancelled():
        completion_kind = "cancelled"
        original: BaseException = RuntimeError(
            "A critical runtime task was cancelled unexpectedly."
        )
    else:
        task_error = task.exception()
        if task_error is None:
            completion_kind = "returned"
            original = RuntimeError("A critical runtime task returned unexpectedly.")
        else:
            completion_kind = "failed"
            original = task_error
    fields: dict[str, object] = {
        "task_name": task.get_name(),
        "completion_kind": completion_kind,
    }
    if completion_kind == "failed":
        fields["failure_type"] = type(original).__name__
    return fields, original


def _new_post_id(_identity: SourceMessageIdentity) -> PostId:
    return PostId(uuid4().hex)


def _create_gateway(loaded: LoadedConfiguration) -> TextIngestionGateway:
    user = loaded.settings.telegram.user
    ingestion = loaded.settings.telegram.ingestion
    return TelethonTextIngestionGateway(
        TelethonSessionAdapter(
            session_path=user.session_path,
            runtime_root=Path("var/sessions"),
            api_id=int(loaded.secrets.get(user.api_id).get_secret_value()),
            api_hash=loaded.secrets.get(user.api_hash).get_secret_value(),
            timeout_seconds=float(ingestion.operation_timeout_seconds),
        )
    )


async def _create_runtime_ingestor(
    loaded: LoadedConfiguration,
    report: TelegramValidationReport,
    gateway: TextIngestionGateway,
    base_ingestor: TextMessageIngestor,
    foundation: FoundationLifecycle,
    clock: Clock,
) -> RuntimeMessageIngestor:
    """Wire existing Milestone 2 components over owned runtime resources."""
    settings = loaded.settings
    validate_unimplemented_ai_flags(
        advertisement_enabled=settings.features.advertisement_detection_enabled,
        semantic_duplicate_enabled=False,
        ai_categorization_enabled=settings.features.ai_scoring_enabled,
    )
    owned_foundation = cast("FullFoundationLifecycle", foundation)
    media_gateway = cast("FullTextIngestionGateway", gateway)
    database = owned_foundation.mongodb_client[settings.mongodb.database_name]
    media = database["media_items"]
    groups = database["media_groups"]
    preparations = database["content_preparations"]
    await initialize_content_preparation_indexes(media, groups, preparations)
    repository = MongoContentPreparationRepository(media, groups, preparations)
    storage = LocalMediaStorage(
        settings.media.root,
        preview_enabled=settings.media.preview_enabled,
    )
    if settings.media.preview_enabled:
        await storage.prepare_preview_directory()
        await storage.backfill_previews(await repository.list_media_for_preview())
    downloader = DownloadPostMedia(
        media_gateway.media_source(),
        storage,
        repository,
        maximum_bytes=settings.media.maximum_bytes,
        timeout_seconds=float(settings.media.download_timeout_seconds),
        maximum_attempts=settings.media.download_max_attempts,
        maximum_rate_limit_delay_seconds=float(
            settings.telegram.ingestion.maximum_flood_wait_seconds
        ),
    )
    assembler = AssembleMediaGroup(
        repository,
        quiet_window=timedelta(seconds=settings.media.album_quiet_seconds),
        maximum_wait=timedelta(seconds=settings.media.album_maximum_wait_seconds),
    )
    validated_by_name = {item.config_name: item for item in report.channels}
    destinations_by_name = {
        item.name: item for item in settings.destination_channels if item.enabled
    }
    source_policies: list[RuntimeSourcePolicy] = []
    for source in settings.source_channels:
        if not source.enabled:
            continue
        validated = validated_by_name[source.name]
        if source.default_category_id is None:
            raise ValueError("An enabled source requires a default category.")
        destination_specs: list[DestinationSpec] = []
        for name in source.allowed_destination_names:
            destination = destinations_by_name[name]
            resolved = validated_by_name[name].channel
            username = resolved.username or destination.username
            if username is None:
                raise ValueError("A destination username is required for preparation.")
            destination_specs.append(DestinationSpec(name, username.removeprefix("@")))
        source_username = validated.channel.username or source.username
        source_policies.append(
            RuntimeSourcePolicy(
                validated.channel.channel_id,
                source_username.removeprefix("@"),
                source.default_category_id,
                tuple(destination_specs),
            )
        )
    policy = RuntimePreparationPolicy(
        categories=tuple(
            Category(item.category_id, item.display_name)
            for item in settings.categorization.categories
        ),
        category_rules=tuple(
            KeywordCategoryRule(
                item.rule_id, item.category_id, item.keyword, item.priority
            )
            for item in settings.categorization.keyword_rules
        ),
        sources=tuple(source_policies),
    )
    correlation_id = foundation.correlation_id
    if correlation_id is None:
        raise ValueError("Runtime correlation context is unavailable.")
    return RuntimeMessageIngestor(
        post_ingestor=base_ingestor,
        post_repository=foundation.repository,
        content_repository=repository,
        storage=storage,
        downloader=downloader,
        assembler=assembler,
        pipeline=PreparePostPipeline(repository),
        policy=policy,
        clock=clock,
        logger=foundation.logger,
        correlation_id=correlation_id,
        album_finalization_owner=f"album-finalizer-{uuid4().hex}",
        album_finalization_max_attempts=(
            settings.media.album_finalization_max_attempts
        ),
        album_finalization_retry_delay=timedelta(
            seconds=settings.media.album_finalization_retry_seconds
        ),
        album_finalization_lease=timedelta(
            seconds=settings.media.album_finalization_lease_seconds
        ),
    )


def create_text_ingestion_application(
    *,
    sink: EventSink,
    clock: Clock | None = None,
) -> TextIngestionApplication:
    """Build an inert concrete Milestone 1 lifecycle."""
    dependencies = TextIngestionDependencies(
        foundation=create_foundation_application(sink=sink),
        gateway_factory=_create_gateway,
        clock=clock or SystemClock(),
        post_id_factory=_new_post_id,
        sleeper=asyncio.sleep,
        jitter_source=lambda: 0.5,
        runtime_ingestor_factory=_create_runtime_ingestor,
        runtime_worker_factory=None,
    )
    return TextIngestionApplication(dependencies)


async def _create_publication_worker(
    loaded: LoadedConfiguration,
    report: TelegramValidationReport,
    gateway: TextIngestionGateway,
    foundation: FoundationLifecycle,
) -> RuntimeWorker:
    """Wire immediate and native scheduling over the ingestion-owned client."""
    settings = loaded.settings
    owned_foundation = cast("FullFoundationLifecycle", foundation)
    owned_gateway = cast("FullTextIngestionGateway", gateway)
    database = owned_foundation.mongodb_client[settings.mongodb.database_name]
    publications = database["publications"]
    schedules = database["scheduled_publications"]
    queues = database["schedule_queues"]
    native_commands = database["native_schedule_commands"]
    native_leases = database["native_schedule_destination_leases"]
    deliveries = database["approval_deliveries"]
    media = database["media_items"]
    groups = database["media_groups"]
    preparations = database["content_preparations"]
    await initialize_publication_indexes(publications, schedules, queues)
    await initialize_native_schedule_indexes(native_commands, native_leases)
    await initialize_operational_approval_indexes(deliveries)
    content_repository = MongoContentPreparationRepository(media, groups, preparations)
    destination_ids = {
        item.channel.channel_id
        for item in report.channels
        if item.role.value == "Destination"
    }
    destination_names = {
        value.telegram_channel_id: value.name
        for value in settings.destination_channels
        if value.enabled and value.telegram_channel_id in destination_ids
    }
    loader = MongoPublicationPayloadLoader(
        content_repository,
        database["posts"],
        media,
        groups,
        destination_names=destination_names,
    )
    publisher = owned_gateway.publisher(media_root=settings.media.root)
    publication_repository = MongoPublicationRepository(publications)
    schedule_repository = MongoScheduleRepository(schedules, queues)
    native_repository = MongoNativeScheduleRepository(native_commands, native_leases)
    operational = MongoOperationalApprovalRepository(preparations, deliveries)
    owner = f"runtime-{uuid4().hex}"
    heartbeat = MongoRuntimeHeartbeatRepository(database["runtime_heartbeats"])
    publishing = settings.publishing
    publication = PublishImmediately(
        publication_repository,
        publisher,
        clock=lambda: datetime.now(UTC),
        timeout_seconds=float(publishing.operation_timeout_seconds),
        lease_seconds=float(publishing.publication_lease_seconds),
        max_attempts=publishing.publication_max_attempts,
        initial_delay_seconds=float(publishing.retry_initial_delay_seconds),
        maximum_delay_seconds=float(publishing.retry_maximum_delay_seconds),
    )

    def request_builder(action: str) -> Callable[[str, int], Awaitable[PublishRequest]]:
        async def build(post_id: str, destination_id: int) -> PublishRequest:
            return PublishRequest(
                post_id,
                destination_id,
                await loader.load(post_id, destination_id),
                owner,
                uuid4().hex,
                True,
                True,
                True,
                True,
                True,
                destination_id in destination_ids,
                action=action,
            )

        return build

    async def after_result(job: ScheduledPublication, status: RunDueStatus) -> None:
        if status not in {RunDueStatus.COMPLETED, RunDueStatus.FAILED}:
            return
        await operational.record_destination_status(
            job.post_id,
            job.destination_id,
            status="published"
            if status is RunDueStatus.COMPLETED
            else "permanent_failed",
            version=job.version + 1,
            at=datetime.now(UTC),
            action=job.action,
            due_at=job.due_at,
        )

    async def before_attempt(job: ScheduledPublication) -> None:
        await operational.record_destination_status(
            job.post_id,
            job.destination_id,
            status="publishing",
            version=job.version + 1,
            at=datetime.now(UTC),
            action=job.action,
            due_at=job.due_at,
        )

    immediate = RunDuePublication(
        schedule_repository,
        owner=owner,
        clock=lambda: datetime.now(UTC),
        lease_seconds=float(publishing.publication_lease_seconds),
        max_attempts=publishing.publication_max_attempts,
        retry_delay_seconds=float(publishing.retry_initial_delay_seconds),
        action="immediate",
        build_request=request_builder("immediate"),
        publish=publication.execute,
        after_result=after_result,
        before_attempt=before_attempt,
        logger=owned_foundation.logger,
    )
    native_gateway = cast(
        "TelegramNativeSchedulerGateway",
        owned_gateway.native_scheduler(media_root=settings.media.root),
    )

    async def after_native_scheduled(command: NativeScheduleCommand) -> None:
        await operational.record_destination_status(
            command.post_id,
            command.destination_id,
            status="native_scheduled",
            version=command.selection_version,
            at=datetime.now(UTC),
            action="scheduled",
            due_at=command.due_at,
        )

    async def after_native_cancelled(command: NativeScheduleCommand) -> None:
        now = datetime.now(UTC)
        if command.follow_up_immediate:
            reservation = await schedule_repository.reserve_immediate(
                job_id=publication_identity(
                    command.post_id, command.destination_id, "immediate"
                ),
                post_id=command.post_id,
                destination_id=command.destination_id,
                now=now,
            )
            await operational.record_destination_status(
                command.post_id,
                command.destination_id,
                status="immediate_queued",
                version=command.selection_version,
                at=now,
                action="immediate",
                due_at=reservation.job.due_at,
            )
        else:
            await operational.record_destination_status(
                command.post_id,
                command.destination_id,
                status="cancelled",
                version=command.selection_version,
                at=now,
            )

    async def after_native_reconciled(command: NativeScheduleCommand) -> None:
        status = {
            "cancelled_external": "cancelled",
            "resolved": "native_resolved",
            "outcome_unknown": "native_outcome_unknown",
        }.get(command.status.value, "native_scheduled")
        await operational.record_destination_status(
            command.post_id,
            command.destination_id,
            status=status,
            version=command.selection_version,
            at=datetime.now(UTC),
            action="scheduled",
            due_at=command.due_at,
        )

    native = RunNativeScheduling(
        native_repository,
        native_gateway,
        loader,
        owner=owner,
        clock=lambda: datetime.now(UTC),
        timeout_seconds=float(publishing.native_schedule_timeout_seconds),
        lease_seconds=float(publishing.native_schedule_lease_seconds),
        retry_seconds=float(publishing.retry_initial_delay_seconds),
        after_scheduled=after_native_scheduled,
        after_cancelled=after_native_cancelled,
        after_reconciled=after_native_reconciled,
    )

    async def run_once() -> None:
        await immediate.execute_once()
        await native.execute_once()
        await native.reconcile_once()

    publication_poll_seconds = min(
        1.0,
        float(publishing.worker_poll_seconds),
        float(publishing.native_schedule_poll_seconds),
    )
    publication_worker = ScheduledPublicationWorker(
        run_once, poll_seconds=publication_poll_seconds
    )
    ready = asyncio.Event()

    class OperationalWorker:
        async def wait_ready(self) -> None:
            """Wait until the first heartbeat and publication loop are active."""
            await ready.wait()

        async def run(self) -> None:
            """Supervise heartbeat and publication as independent critical tasks."""
            started_at = datetime.now(UTC)
            heartbeat_interval = max(
                1.0, min(5.0, float(publishing.worker_poll_seconds))
            )
            heartbeat_ready = asyncio.Event()
            publication_ready = asyncio.Event()

            async def pulse() -> None:
                await heartbeat.beat(
                    owner,
                    started_at=started_at,
                    now=datetime.now(UTC),
                    status="running",
                )
                owned_foundation.logger.emit(
                    level=LogLevel.INFO, event_name="runtime_heartbeat_active"
                )
                heartbeat_ready.set()
                while True:
                    await asyncio.sleep(heartbeat_interval)
                    await heartbeat.beat(
                        owner,
                        started_at=started_at,
                        now=datetime.now(UTC),
                        status="running",
                    )

            async def publish_due() -> None:
                owned_foundation.logger.emit(
                    level=LogLevel.INFO,
                    event_name="publication_worker_started",
                    fields={"publication_poll_seconds": publication_poll_seconds},
                )
                publication_ready.set()
                await publication_worker.run()

            publication_task = asyncio.create_task(
                publish_due(), name="runtime-publication"
            )
            heartbeat_task = asyncio.create_task(pulse(), name="runtime-heartbeat")

            async def await_readiness() -> None:
                await asyncio.gather(heartbeat_ready.wait(), publication_ready.wait())

            readiness_task = asyncio.create_task(
                await_readiness(), name="runtime-critical-readiness"
            )
            readiness_marker: asyncio.Task[None] | None = None
            try:

                async def mark_ready() -> None:
                    await readiness_task
                    ready.set()

                readiness_marker = asyncio.create_task(
                    mark_ready(), name="runtime-readiness-marker"
                )
                done, _pending = await asyncio.wait(
                    (publication_task, heartbeat_task),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                completed = next(iter(done))
                fields, original = _unexpected_task_details(cast_task(completed))
                owned_foundation.logger.emit(
                    level=LogLevel.ERROR,
                    event_name="runtime_task_completed_unexpectedly",
                    fields=fields,
                )
                failure = OperationalRuntimeError(cause=original)
                raise failure from original
            finally:
                tasks = [readiness_task, publication_task, heartbeat_task]
                if readiness_marker is not None:
                    tasks.append(readiness_marker)
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                try:
                    await heartbeat.beat(
                        owner,
                        started_at=started_at,
                        now=datetime.now(UTC),
                        status="stopped",
                    )
                except Exception as error:  # noqa: BLE001 - preserve critical cause.
                    owned_foundation.logger.emit(
                        level=LogLevel.ERROR,
                        event_name="runtime_heartbeat_stop_failed",
                        fields={"failure_type": type(error).__name__},
                    )

    return OperationalWorker()


def create_operational_runtime_application(
    *, sink: EventSink
) -> TextIngestionApplication:
    """Build full ingestion and publication under one User API session owner."""
    dependencies = TextIngestionDependencies(
        foundation=create_foundation_application(sink=sink),
        gateway_factory=_create_gateway,
        clock=SystemClock(),
        post_id_factory=_new_post_id,
        sleeper=asyncio.sleep,
        jitter_source=lambda: 0.5,
        runtime_ingestor_factory=_create_runtime_ingestor,
        runtime_worker_factory=_create_publication_worker,
    )
    return TextIngestionApplication(dependencies)


async def run_text_ingestion_application(
    application: TextIngestionApplication,
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
) -> FoundationExitCode:
    """Run until listeners finish or cancellation, then always shut down."""
    try:
        await application.start(configuration_path, environ=environ)
        await application.wait()
    except asyncio.CancelledError:
        await application.shutdown()
        raise
    except TextIngestionStartupError as error:
        await application.shutdown()
        if isinstance(error.__cause__, FoundationStartupError):
            return error.__cause__.exit_code
        return FoundationExitCode.INFRASTRUCTURE_ERROR
    except Exception:  # noqa: BLE001 - safe long-running CLI boundary.
        await application.shutdown()
        return FoundationExitCode.INFRASTRUCTURE_ERROR
    await application.shutdown()
    return FoundationExitCode.SUCCESS


__all__ = (
    "FoundationLifecycle",
    "SystemClock",
    "TextIngestionApplication",
    "TextIngestionDependencies",
    "TextIngestionGateway",
    "TextIngestionStartupError",
    "create_operational_runtime_application",
    "create_text_ingestion_application",
    "run_text_ingestion_application",
)
