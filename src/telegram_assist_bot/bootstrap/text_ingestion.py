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
from telegram_assist_bot.application.ports import (
    Clock,
    MediaSource,
    PostRepository,
    ResolvedTelegramChannel,
    TelegramHistoryGateway,
    TelegramLiveGateway,
    TelegramLiveSubscription,
    TelegramValidationGateway,
)
from telegram_assist_bot.application.prepare_post_pipeline import (
    DestinationSpec,
    PreparePostPipeline,
    validate_unimplemented_ai_flags,
)
from telegram_assist_bot.application.runtime_ingestion import (
    RuntimeMessageIngestor,
    RuntimePreparationPolicy,
    RuntimeSourcePolicy,
)
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
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
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
from telegram_assist_bot.workers import LiveTextListener

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pymongo import AsyncMongoClient

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

    async def shutdown(self) -> None:
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


class TextIngestionApplication:
    """Own validation, subscribe-before-crawl, listener, and reverse cleanup."""

    __slots__ = (
        "_crawl_results",
        "_dependencies",
        "_foundation_owned",
        "_gateway",
        "_listener_tasks",
        "_runtime_ingestor",
        "_shutdown_task",
        "_state",
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
        self._crawl_results: dict[int, CrawlTodayResult] = {}
        self._shutdown_task: asyncio.Task[None] | None = None

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
                            "error_category": issue.error_category,
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
            for source in sources:
                crawler = CrawlTodayTextPosts(
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
                result = await crawler.execute(
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
                self._listener_tasks.append(cast_task(task))
                self._subscriptions.remove(prepared[source.channel_id])
            if self._runtime_ingestor is not None:
                album_task = asyncio.create_task(
                    self._run_album_finalizer(), name="telegram-album-finalizer"
                )
                self._listener_tasks.append(cast_task(album_task))
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
            self._state = _State.FAILED
            await self._cleanup()
            raise
        except Exception as error:
            self._state = _State.FAILED
            await self._cleanup()
            if isinstance(error, TextIngestionStartupError):
                raise
            raise TextIngestionStartupError(cause=error) from error

    async def wait(self) -> None:
        """Wait for all live listeners and propagate their first failure."""
        if not self.is_ready:
            raise TextIngestionStartupError
        if self._listener_tasks:
            await asyncio.gather(*self._listener_tasks)

    async def shutdown(self) -> None:
        """Cancel listeners and close every owned resource exactly once."""
        if self._shutdown_task is None:
            self._shutdown_task = asyncio.create_task(self._cleanup())
        await self._shutdown_task

    async def _cleanup(self) -> None:
        if self._state is _State.STOPPED:
            return
        self._state = _State.STOPPING
        for task in reversed(self._listener_tasks):
            if not task.done():
                task.cancel()
        if self._listener_tasks:
            await asyncio.gather(*self._listener_tasks, return_exceptions=True)
        self._listener_tasks.clear()
        for subscription in reversed(self._subscriptions):
            await subscription.close()
        self._subscriptions.clear()
        if self._gateway is not None:
            await self._gateway.close()
            self._gateway = None
        if self._foundation_owned:
            await self._dependencies.foundation.shutdown()
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
                    "error_category": getattr(error, "error_category", "permanent")
                },
                error=safe_error,
            )
            raise safe_error from error


def cast_task(task: asyncio.Task[object]) -> asyncio.Task[object]:
    """Keep task storage explicit for strict type checking."""
    return task


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
    "create_text_ingestion_application",
    "run_text_ingestion_application",
)
