"""Composition root and lifecycle for Milestone 1 text ingestion."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import IntEnum
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, Self
from uuid import uuid4

from telegram_assist_bot.application import (
    CrawlTodayResult,
    CrawlTodayTextPosts,
    HandleLiveMessage,
    IngestPostIdempotently,
)
from telegram_assist_bot.application.ports import (
    Clock,
    PostRepository,
    ResolvedTelegramChannel,
    TelegramHistoryGateway,
    TelegramLiveGateway,
    TelegramLiveSubscription,
    TelegramValidationGateway,
)
from telegram_assist_bot.bootstrap.runtime import (
    FoundationExitCode,
    FoundationStartupError,
    create_foundation_application,
)
from telegram_assist_bot.bootstrap.telegram_validation import validate_telegram_startup
from telegram_assist_bot.domain.posts import PostId, SourceMessageIdentity
from telegram_assist_bot.infrastructure.telegram.user import (
    TelethonSessionAdapter,
    TelethonTextIngestionGateway,
)
from telegram_assist_bot.shared.config import LoadedConfiguration, LogLevel
from telegram_assist_bot.shared.retry import RetryPolicy
from telegram_assist_bot.workers import LiveTextListener

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

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


class SystemClock(Clock):
    """Return current UTC time for concrete composition."""

    def utc_now(self) -> datetime:
        """Return one timezone-aware UTC instant."""
        return datetime.now(UTC)


type GatewayFactory = Callable[[LoadedConfiguration], TextIngestionGateway]
type PostIdFactory = Callable[[SourceMessageIdentity], PostId]


@dataclass(frozen=True, slots=True)
class TextIngestionDependencies:
    """Hold explicit collaborators for one text-ingestion lifecycle."""

    foundation: FoundationLifecycle = field(repr=False)
    gateway_factory: GatewayFactory = field(repr=False)
    clock: Clock = field(repr=False)
    post_id_factory: PostIdFactory = field(repr=False)
    sleeper: AsyncSleeper = field(repr=False)
    jitter_source: JitterSource = field(repr=False)


class TextIngestionApplication:
    """Own validation, subscribe-before-crawl, listener, and reverse cleanup."""

    __slots__ = (
        "_crawl_results",
        "_dependencies",
        "_foundation_owned",
        "_gateway",
        "_listener_tasks",
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
            report = await validate_telegram_startup(loaded.settings, gateway)
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
            for source in sources:
                ingestor = IngestPostIdempotently(
                    self._dependencies.foundation.repository,
                    self._dependencies.clock,
                    self._dependencies.post_id_factory,
                    logger,
                )
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
            self._state = _State.READY
            logger.emit(
                level=LogLevel.INFO,
                event_name="text_ingestion_ready",
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
