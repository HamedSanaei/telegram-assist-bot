"""Explicit composition root and asynchronous foundation lifecycle."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import IntEnum
from typing import TYPE_CHECKING, Protocol, Self
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoPostRepository,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    initialize_post_indexes,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    ConfigurationError,
    LoadedConfiguration,
    LogLevel,
    MongoConfig,
    ResolvedSecrets,
    load_configuration,
)
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    EventClock,
    EventSink,
    Redactor,
    StructuredEvent,
    StructuredLogger,
    bind_log_context,
    format_json_event,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.application.ports import PostRepository
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )


class FoundationExitCode(IntEnum):
    """Define the stable process exit codes owned by the foundation CLI."""

    SUCCESS = 0
    CONFIGURATION_ERROR = 2
    INFRASTRUCTURE_ERROR = 3


class FoundationStartupError(RuntimeError):
    """Base class for safe startup failures returned to the CLI boundary."""

    exit_code: FoundationExitCode = FoundationExitCode.INFRASTRUCTURE_ERROR
    error_category = "permanent"
    safe_message = "Application foundation startup failed."

    def __init__(self, *, cause: BaseException | None = None) -> None:
        """Retain an optional cause without copying its message."""
        super().__init__(self.safe_message)
        if cause is not None:
            self.__cause__ = cause


class FoundationConfigurationError(FoundationStartupError):
    """Report a configuration or safe command-line startup failure."""

    exit_code = FoundationExitCode.CONFIGURATION_ERROR
    error_category = "configuration"
    safe_message = "Application configuration could not be loaded."


class FoundationInfrastructureError(FoundationStartupError):
    """Report a safe infrastructure startup or shutdown failure."""

    exit_code = FoundationExitCode.INFRASTRUCTURE_ERROR
    safe_message = "Application infrastructure could not be initialized."


class FoundationLifecycleError(RuntimeError):
    """Report invalid use of one foundation lifecycle instance."""

    def __init__(self) -> None:
        """Initialize a state-independent and non-sensitive message."""
        super().__init__("Foundation lifecycle is not in the required state.")


class EventOutputError(RuntimeError):
    """Report an unavailable structured-event output without stream details."""

    def __init__(self) -> None:
        """Initialize a fixed message that cannot retain event contents."""
        super().__init__("Structured event output failed.")


class BinaryEventStream(Protocol):
    """Describe the binary UTF-8 stream required by the CLI event sink."""

    def write(self, data: bytes, /) -> int:
        """Write one encoded event payload."""
        ...

    def flush(self) -> None:
        """Flush buffered event bytes."""
        ...


class JsonLineEventSink:
    """Write already-structured events as redacted UTF-8 JSON lines."""

    __slots__ = ("_redactor", "_stream")

    def __init__(self, stream: BinaryEventStream, *, redactor: Redactor) -> None:
        """Store an injected output stream and the existing redaction policy."""
        self._stream = stream
        self._redactor = redactor

    def __call__(self, event: StructuredEvent) -> None:
        """Encode and write one event without exposing output failure details."""
        try:
            payload = format_json_event(event, redactor=self._redactor)
            encoded = f"{payload}\n".encode("utf-8")  # noqa: UP012
            if self._stream.write(encoded) != len(encoded):
                raise EventOutputError
            self._stream.flush()
        except Exception:  # noqa: BLE001 - map an injected sink boundary safely.
            raise EventOutputError from None


class ConfigurationLoader(Protocol):
    """Load one immutable configuration snapshot without external I/O."""

    def __call__(
        self,
        path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> LoadedConfiguration:
        """Load and validate one configuration path."""
        ...


class LoggerFactory(Protocol):
    """Build one structured logger after configuration is validated."""

    def __call__(
        self,
        minimum_level: LogLevel,
        /,
        *,
        secret_values: tuple[str, ...],
    ) -> StructuredLogger:
        """Return a logger configured for the supplied minimum level."""
        ...


class MongoClientFactory(Protocol):
    """Create the lazy MongoDB resource owned by one lifecycle."""

    def __call__(
        self,
        config: MongoConfig,
        secrets: ResolvedSecrets,
        /,
    ) -> AsyncMongoClient[MongoDocument]:
        """Return one configured MongoDB client."""
        ...


class MongoConnectionVerifier(Protocol):
    """Verify MongoDB connectivity within a configured timeout."""

    async def __call__(
        self,
        client: AsyncMongoClient[MongoDocument],
        *,
        timeout_seconds: int,
    ) -> None:
        """Verify the owned client and supported server version."""
        ...


class MongoCollectionFactory(Protocol):
    """Resolve the existing posts collection from an owned client."""

    def __call__(
        self,
        client: AsyncMongoClient[MongoDocument],
        config: MongoConfig,
        /,
    ) -> AsyncCollection[MongoDocument]:
        """Return the configured posts collection handle."""
        ...


class MongoIndexInitializer(Protocol):
    """Initialize the exact restart-safe T004 post indexes."""

    async def __call__(
        self,
        collection: AsyncCollection[MongoDocument],
        *,
        timeout_seconds: int,
    ) -> None:
        """Create or verify the existing post index definitions."""
        ...


class PostRepositoryFactory(Protocol):
    """Construct the concrete post repository after indexes are ready."""

    def __call__(
        self,
        collection: AsyncCollection[MongoDocument],
        timeout_seconds: int,
        /,
    ) -> PostRepository:
        """Return one application-owned repository implementation."""
        ...


class MongoClientCloser(Protocol):
    """Close one owned client within the configured timeout."""

    async def __call__(
        self,
        client: AsyncMongoClient[MongoDocument],
        *,
        timeout_seconds: int,
    ) -> None:
        """Close one MongoDB client resource."""
        ...


@dataclass(frozen=True, slots=True)
class FoundationDependencies:
    """Hold explicit collaborators used by one composition-root lifecycle."""

    configuration_loader: ConfigurationLoader
    logger_factory: LoggerFactory
    mongo_client_factory: MongoClientFactory
    mongo_connection_verifier: MongoConnectionVerifier
    mongo_collection_factory: MongoCollectionFactory
    mongo_index_initializer: MongoIndexInitializer
    post_repository_factory: PostRepositoryFactory
    mongo_client_closer: MongoClientCloser
    correlation_id_factory: Callable[[], str]


class _LifecycleState(IntEnum):
    NEW = 0
    STARTING = 1
    READY = 2
    STOPPING = 3
    STOPPED = 4
    FAILED = 5


class FoundationApplication:
    """Own one explicit foundation startup and idempotent shutdown lifecycle."""

    __slots__ = (
        "_application_logger",
        "_client",
        "_context",
        "_dependencies",
        "_logger",
        "_repository",
        "_shutdown_task",
        "_state",
        "_timeout_seconds",
    )

    def __init__(self, dependencies: FoundationDependencies) -> None:
        """Create an inert lifecycle without opening any external resource."""
        self._dependencies = dependencies
        self._state = _LifecycleState.NEW
        self._client: AsyncMongoClient[MongoDocument] | None = None
        self._timeout_seconds: int | None = None
        self._repository: PostRepository | None = None
        self._application_logger: StructuredLogger | None = None
        self._logger: StructuredLogger | None = None
        self._context: CorrelationContext | None = None
        self._shutdown_task: asyncio.Task[None] | None = None

    @property
    def is_ready(self) -> bool:
        """Return whether configuration, ping, indexes, and wiring succeeded."""
        return self._state is _LifecycleState.READY

    @property
    def repository(self) -> PostRepository:
        """Return the wired repository only while the lifecycle is ready."""
        if not self.is_ready or self._repository is None:
            raise FoundationLifecycleError
        return self._repository

    @property
    def logger(self) -> StructuredLogger:
        """Return the configured application logger only while ready."""
        if not self.is_ready or self._application_logger is None:
            raise FoundationLifecycleError
        return self._application_logger

    @property
    def correlation_id(self) -> str | None:
        """Return the current lifecycle correlation identifier when allocated."""
        return None if self._context is None else self._context.correlation_id

    async def start(
        self,
        configuration_path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        """Start the foundation in fail-fast order and return the ready instance."""
        if self._state is not _LifecycleState.NEW:
            raise FoundationLifecycleError
        self._state = _LifecycleState.STARTING
        try:
            self._context = CorrelationContext(
                correlation_id=self._dependencies.correlation_id_factory()
            )
        except asyncio.CancelledError:
            self._state = _LifecycleState.FAILED
            raise
        except Exception as error:
            self._state = _LifecycleState.FAILED
            raise FoundationInfrastructureError(cause=error) from error

        try:
            loaded = self._dependencies.configuration_loader(
                configuration_path,
                environ=environ,
            )
        except asyncio.CancelledError:
            self._state = _LifecycleState.FAILED
            raise
        except ConfigurationError as error:
            self._state = _LifecycleState.FAILED
            self._report_configuration_failure(error)
            raise FoundationConfigurationError(cause=error) from error
        except Exception as error:
            self._state = _LifecycleState.FAILED
            self._report_unexpected_configuration_failure(error)
            raise FoundationInfrastructureError(cause=error) from error

        try:
            secret_values = _configuration_redaction_values(loaded)
            self._application_logger = self._dependencies.logger_factory(
                loaded.settings.logging.level,
                secret_values=secret_values,
            )
            self._logger = self._dependencies.logger_factory(
                LogLevel.DEBUG,
                secret_values=secret_values,
            )
        except asyncio.CancelledError:
            self._state = _LifecycleState.FAILED
            raise
        except Exception as error:
            self._state = _LifecycleState.FAILED
            raise FoundationInfrastructureError(cause=error) from error

        if self._logger is None:
            raise AssertionError("foundation logging context was not initialized")

        with bind_log_context(self._context):
            try:
                self._logger.emit(level=LogLevel.INFO, event_name="startup_begun")
                self._logger.emit(
                    level=LogLevel.INFO,
                    event_name="configuration_validation_succeeded",
                )
                self._logger.emit(
                    level=LogLevel.INFO,
                    event_name="logging_initialized",
                    fields={"minimum_level": loaded.settings.logging.level.value},
                )
                await self._start_mongodb(loaded)
            except asyncio.CancelledError as cancellation:
                self._state = _LifecycleState.FAILED
                self._best_effort_emit(
                    level=LogLevel.WARNING,
                    event_name="startup_cancelled",
                )
                await self._cleanup_preserving(cancellation)
                raise
            except Exception as error:
                self._state = _LifecycleState.FAILED
                self._best_effort_emit(
                    level=LogLevel.ERROR,
                    event_name="startup_failed",
                    error=error,
                )
                await self._cleanup_preserving(error)
                raise FoundationInfrastructureError(cause=error) from error

        return self

    async def shutdown(self) -> None:
        """Close each owned resource once; repeated or concurrent calls are safe."""
        if self._state is _LifecycleState.STARTING:
            raise FoundationLifecycleError
        task = self._ensure_shutdown_task(reason="requested")
        if task is None:
            if self._state is _LifecycleState.NEW:
                self._state = _LifecycleState.STOPPED
            return
        await _wait_for_task_completion(task)

    async def _start_mongodb(self, loaded: LoadedConfiguration) -> None:
        config = loaded.settings.mongodb
        timeout_seconds = config.connect_timeout_seconds
        client = self._dependencies.mongo_client_factory(config, loaded.secrets)
        self._client = client
        self._timeout_seconds = timeout_seconds

        await self._dependencies.mongo_connection_verifier(
            client,
            timeout_seconds=timeout_seconds,
        )
        if self._logger is None:
            raise AssertionError("foundation logger is unavailable")
        self._logger.emit(level=LogLevel.INFO, event_name="mongodb_connected")

        collection = self._dependencies.mongo_collection_factory(client, config)
        await self._dependencies.mongo_index_initializer(
            collection,
            timeout_seconds=timeout_seconds,
        )
        self._logger.emit(level=LogLevel.INFO, event_name="indexes_ready")

        self._repository = self._dependencies.post_repository_factory(
            collection,
            timeout_seconds,
        )
        self._logger.emit(level=LogLevel.INFO, event_name="application_ready")
        self._state = _LifecycleState.READY

    def _report_configuration_failure(self, error: ConfigurationError) -> None:
        try:
            self._logger = self._dependencies.logger_factory(
                LogLevel.DEBUG,
                secret_values=(),
            )
        except Exception:  # noqa: BLE001 - fallback reporting cannot block failure.
            return
        if self._context is None:
            return
        with bind_log_context(self._context):
            self._best_effort_emit(level=LogLevel.INFO, event_name="startup_begun")
            self._best_effort_emit(
                level=LogLevel.ERROR,
                event_name="configuration_validation_failed",
                error=error,
            )
            self._best_effort_emit(
                level=LogLevel.ERROR,
                event_name="startup_failed",
                error=error,
            )

    def _report_unexpected_configuration_failure(self, error: Exception) -> None:
        try:
            self._logger = self._dependencies.logger_factory(
                LogLevel.DEBUG,
                secret_values=(),
            )
        except Exception:  # noqa: BLE001 - fallback reporting cannot block failure.
            return
        if self._context is None:
            return
        safe_error = FoundationInfrastructureError(cause=error)
        with bind_log_context(self._context):
            self._best_effort_emit(level=LogLevel.INFO, event_name="startup_begun")
            self._best_effort_emit(
                level=LogLevel.ERROR,
                event_name="configuration_validation_failed",
                error=safe_error,
            )
            self._best_effort_emit(
                level=LogLevel.ERROR,
                event_name="startup_failed",
                error=safe_error,
            )

    def _best_effort_emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> bool:
        logger = self._logger
        if logger is None:
            return False
        try:
            logger.emit(
                level=level,
                event_name=event_name,
                fields=fields,
                error=error,
            )
        except Exception:  # noqa: BLE001 - failure reporting must not mask cleanup.
            return False
        return True

    def _ensure_shutdown_task(self, *, reason: str) -> asyncio.Task[None] | None:
        if self._client is None:
            return self._shutdown_task
        if self._shutdown_task is None:
            self._state = _LifecycleState.STOPPING
            self._shutdown_task = asyncio.create_task(
                self._shutdown_once(reason=reason)
            )
        return self._shutdown_task

    async def _shutdown_once(self, *, reason: str) -> None:
        client = self._client
        timeout_seconds = self._timeout_seconds
        context = self._context
        if client is None or timeout_seconds is None or context is None:
            self._state = _LifecycleState.STOPPED
            return

        logging_failed = False
        close_error: Exception | None = None
        with bind_log_context(context):
            logging_failed = not self._best_effort_emit(
                level=LogLevel.INFO,
                event_name="shutdown_begun",
                fields={"reason": reason},
            )
            try:
                await self._dependencies.mongo_client_closer(
                    client,
                    timeout_seconds=timeout_seconds,
                )
            except asyncio.CancelledError:
                self._state = _LifecycleState.FAILED
                self._shutdown_task = None
                raise
            except Exception as error:  # noqa: BLE001 - close is an injected boundary.
                close_error = error
            else:
                self._client = None
                self._timeout_seconds = None
                self._repository = None
                self._application_logger = None
                self._state = _LifecycleState.STOPPED

            if close_error is not None:
                self._client = None
                self._timeout_seconds = None
                self._repository = None
                self._application_logger = None
                self._state = _LifecycleState.STOPPED

            if close_error is None:
                logging_failed = (
                    not self._best_effort_emit(
                        level=LogLevel.INFO,
                        event_name="resource_closed",
                        fields={"resource_type": "mongodb_client"},
                    )
                    or logging_failed
                )
                logging_failed = (
                    not self._best_effort_emit(
                        level=LogLevel.INFO,
                        event_name="shutdown_completed",
                    )
                    or logging_failed
                )
            else:
                self._best_effort_emit(
                    level=LogLevel.ERROR,
                    event_name="shutdown_failed",
                    fields={"resource_type": "mongodb_client"},
                    error=close_error,
                )

        if close_error is not None:
            raise FoundationInfrastructureError(cause=close_error) from close_error
        if logging_failed:
            raise FoundationInfrastructureError

    async def _cleanup_preserving(self, original_error: BaseException) -> None:
        task = self._ensure_shutdown_task(reason="startup_failure")
        if task is None:
            return
        try:
            await _wait_for_task_completion(task)
        except asyncio.CancelledError:
            if isinstance(original_error, asyncio.CancelledError):
                original_error.add_note(
                    "Additional cancellation arrived during safe cleanup."
                )
                return
            raise
        except Exception:  # noqa: BLE001 - preserve the primary startup failure.
            original_error.add_note("Foundation startup cleanup failed safely.")


async def _wait_for_task_completion(task: asyncio.Task[None]) -> None:
    cancellation: asyncio.CancelledError | None = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as error:
            cancellation = error
        except Exception:  # noqa: BLE001 - retrieve the exact task failure below.
            break

    try:
        task.result()
    except asyncio.CancelledError:
        if cancellation is not None:
            raise cancellation from None
        raise
    except Exception:
        if cancellation is not None:
            cancellation.add_note("Shutdown failed after cancellation.")
            raise cancellation from None
        raise
    if cancellation is not None:
        raise cancellation


def _configuration_redaction_values(loaded: LoadedConfiguration) -> tuple[str, ...]:
    settings = loaded.settings
    references = [
        settings.mongodb.uri,
        settings.telegram.user.api_id,
        settings.telegram.user.api_hash,
        settings.telegram.user.phone_number,
        settings.telegram.bot.token,
        *(
            provider.api_key
            for provider in settings.ai.providers
            if provider.api_key is not None
        ),
    ]
    values: set[str] = set()
    for reference in references:
        value = loaded.secrets.get(reference).get_secret_value()
        values.add(value)
        try:
            parsed = urlsplit(value)
        except ValueError:
            continue
        if parsed.username:
            values.add(unquote(parsed.username))
        if parsed.password:
            values.add(unquote(parsed.password))
    return tuple(sorted(values, key=lambda item: (-len(item), item)))


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_correlation_id() -> str:
    return uuid4().hex


def create_foundation_application(
    *,
    sink: EventSink,
    clock: EventClock = _utc_now,
    correlation_id_factory: Callable[[], str] = _new_correlation_id,
) -> FoundationApplication:
    """Build an inert lifecycle wired to the existing concrete foundation."""

    def logger_factory(
        minimum_level: LogLevel,
        *,
        secret_values: tuple[str, ...],
    ) -> StructuredLogger:
        return StructuredLogger(
            sink=sink,
            clock=clock,
            redactor=Redactor(secret_values=secret_values),
            minimum_level=minimum_level,
        )

    dependencies = FoundationDependencies(
        configuration_loader=load_configuration,
        logger_factory=logger_factory,
        mongo_client_factory=create_mongodb_client,
        mongo_connection_verifier=verify_mongodb_connection,
        mongo_collection_factory=get_posts_collection,
        mongo_index_initializer=initialize_post_indexes,
        post_repository_factory=MongoPostRepository,
        mongo_client_closer=close_mongodb_client,
        correlation_id_factory=correlation_id_factory,
    )
    return FoundationApplication(dependencies)


__all__ = (
    "BinaryEventStream",
    "ConfigurationLoader",
    "EventOutputError",
    "FoundationApplication",
    "FoundationConfigurationError",
    "FoundationDependencies",
    "FoundationExitCode",
    "FoundationInfrastructureError",
    "FoundationLifecycleError",
    "FoundationStartupError",
    "JsonLineEventSink",
    "LoggerFactory",
    "MongoClientCloser",
    "MongoClientFactory",
    "MongoCollectionFactory",
    "MongoConnectionVerifier",
    "MongoIndexInitializer",
    "PostRepositoryFactory",
    "create_foundation_application",
)
