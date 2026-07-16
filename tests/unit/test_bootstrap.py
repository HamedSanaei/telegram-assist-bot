"""Verify the worker-free foundation composition root and command-line boundary."""

from __future__ import annotations

import argparse
import ast
import asyncio
import importlib
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, Literal, Self, cast

import pytest

import telegram_assist_bot.__main__ as entry_point_module
import telegram_assist_bot.bootstrap as bootstrap_package
import telegram_assist_bot.bootstrap.cli as cli_module
from telegram_assist_bot.bootstrap.runtime import (
    FoundationApplication,
    FoundationConfigurationError,
    FoundationDependencies,
    FoundationExitCode,
    FoundationInfrastructureError,
    FoundationLifecycleError,
)
from telegram_assist_bot.shared.config import (
    ConfigurationError,
    ConfigurationIssue,
    ConfigurationValidationError,
    LoadedConfiguration,
    LoggingConfig,
    LogLevel,
    MongoConfig,
    ResolvedSecrets,
    load_configuration,
)
from telegram_assist_bot.shared.observability import (
    RedactedValue,
    Redactor,
    StructuredEvent,
    StructuredLogger,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from pymongo import AsyncMongoClient
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.application.ports import PostRepository
    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

type FailureStage = Literal[
    "logger",
    "client",
    "ping",
    "collection",
    "index",
    "repository",
    "close",
]
type BlockingStage = Literal["ping", "index"]

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_CONFIGURATION_PATH = _REPOSITORY_ROOT / "config" / "configuration.example.json"
_FIXED_TIME = datetime(2026, 7, 11, 12, 30, tzinfo=UTC)
_CORRELATION_ID = "correlation-t006-unit"
_SYNTHETIC_ENVIRONMENT = {
    "TAB_MONGODB_URI": "mongodb://database.example.invalid:27017",
    "TAB_TELEGRAM_API_ID": "123456",
    "TAB_TELEGRAM_API_HASH": "fixture-" + "telegram-api-hash-value",
    "TAB_TELEGRAM_PHONE_NUMBER": "synthetic-phone-number",
    "TAB_TELEGRAM_BOT_TOKEN": "fixture-" + "telegram-bot-token-value",
    "TAB_AI_PROVIDER_KEY": "fixture-" + "ai-provider-key-value",
}


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _loaded_configuration(
    environ: Mapping[str, str] = _SYNTHETIC_ENVIRONMENT,
) -> LoadedConfiguration:
    return load_configuration(
        _EXAMPLE_CONFIGURATION_PATH,
        environ=environ,
    )


@dataclass(slots=True)
class _FakeMongoClient:
    marker: str = "fake-mongodb-client"


@dataclass(slots=True)
class _FakeMongoCollection:
    marker: str = "fake-posts-collection"


@dataclass(slots=True)
class _FoundationHarness:
    failure_at: FailureStage | None = None
    configuration_error: ConfigurationError | None = None
    operational_error: Exception | None = None
    block_at: BlockingStage | None = None
    order: list[str] = field(default_factory=list)
    events: list[dict[str, RedactedValue]] = field(default_factory=list)
    requested_path: Path | None = None
    requested_environ: Mapping[str, str] | None = None
    close_count: int = 0
    sink_call_count: int = 0
    sink_failure_at_call: int | None = None
    logger_levels: list[LogLevel] = field(default_factory=list)
    logger_secret_values: list[tuple[str, ...]] = field(default_factory=list)
    stage_entered: asyncio.Event | None = None
    stage_release: asyncio.Event | None = None
    close_entered: asyncio.Event | None = None
    close_release: asyncio.Event | None = None
    loaded: LoadedConfiguration = field(default_factory=_loaded_configuration)
    client: _FakeMongoClient = field(default_factory=_FakeMongoClient)
    collection: _FakeMongoCollection = field(default_factory=_FakeMongoCollection)
    repository_value: object = field(default_factory=object)

    def configuration_loader(
        self,
        path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> LoadedConfiguration:
        self.order.append("configuration")
        self.requested_path = path
        self.requested_environ = environ
        if self.configuration_error is not None:
            raise self.configuration_error
        return self.loaded

    def event_sink(self, event: StructuredEvent) -> None:
        self.sink_call_count += 1
        if self.sink_call_count == self.sink_failure_at_call:
            raise RuntimeError("event sink failed")
        self.events.append(dict(event))

    @staticmethod
    def clock() -> datetime:
        return _FIXED_TIME

    def logger_factory(
        self,
        minimum_level: LogLevel,
        *,
        secret_values: tuple[str, ...],
    ) -> StructuredLogger:
        self.order.append(f"logger:{minimum_level.value}")
        self.logger_levels.append(minimum_level)
        self.logger_secret_values.append(secret_values)
        if self.failure_at == "logger":
            raise RuntimeError("logger factory failed")
        return StructuredLogger(
            sink=self.event_sink,
            clock=self.clock,
            redactor=Redactor(secret_values=secret_values),
            minimum_level=minimum_level,
        )

    def mongo_client_factory(
        self,
        config: MongoConfig,
        secrets: ResolvedSecrets,
    ) -> AsyncMongoClient[MongoDocument]:
        self.order.append("client")
        assert config is self.loaded.settings.mongodb
        assert secrets is self.loaded.secrets
        if self.failure_at == "client":
            raise RuntimeError("client factory failed")
        return cast("AsyncMongoClient[MongoDocument]", self.client)

    async def mongo_connection_verifier(
        self,
        client: AsyncMongoClient[MongoDocument],
        *,
        timeout_seconds: int,
    ) -> None:
        self.order.append("ping")
        assert client is cast("AsyncMongoClient[MongoDocument]", self.client)
        assert timeout_seconds == self.loaded.settings.mongodb.connect_timeout_seconds
        await self._wait_if_blocked("ping")
        if self.failure_at == "ping":
            raise self.operational_error or RuntimeError("ping failed")

    def mongo_collection_factory(
        self,
        client: AsyncMongoClient[MongoDocument],
        config: MongoConfig,
    ) -> AsyncCollection[MongoDocument]:
        self.order.append("collection")
        assert client is cast("AsyncMongoClient[MongoDocument]", self.client)
        assert config is self.loaded.settings.mongodb
        if self.failure_at == "collection":
            raise RuntimeError("collection factory failed")
        return cast("AsyncCollection[MongoDocument]", self.collection)

    async def mongo_index_initializer(
        self,
        collection: AsyncCollection[MongoDocument],
        *,
        timeout_seconds: int,
    ) -> None:
        self.order.append("indexes")
        assert collection is cast(
            "AsyncCollection[MongoDocument]",
            self.collection,
        )
        assert timeout_seconds == self.loaded.settings.mongodb.connect_timeout_seconds
        await self._wait_if_blocked("index")
        if self.failure_at == "index":
            raise self.operational_error or RuntimeError("index setup failed")

    def post_repository_factory(
        self,
        collection: AsyncCollection[MongoDocument],
        timeout_seconds: int,
    ) -> PostRepository:
        self.order.append("repository")
        assert collection is cast(
            "AsyncCollection[MongoDocument]",
            self.collection,
        )
        assert timeout_seconds == self.loaded.settings.mongodb.connect_timeout_seconds
        if self.failure_at == "repository":
            raise RuntimeError("repository factory failed")
        return cast("PostRepository", self.repository_value)

    async def mongo_client_closer(
        self,
        client: AsyncMongoClient[MongoDocument],
        *,
        timeout_seconds: int,
    ) -> None:
        self.order.append("close")
        self.close_count += 1
        assert client is cast("AsyncMongoClient[MongoDocument]", self.client)
        assert timeout_seconds == self.loaded.settings.mongodb.connect_timeout_seconds
        if self.close_entered is not None:
            self.close_entered.set()
        if self.close_release is not None:
            await self.close_release.wait()
        if self.failure_at == "close":
            raise RuntimeError("client close failed")

    async def _wait_if_blocked(self, stage: BlockingStage) -> None:
        if self.block_at != stage:
            return
        if self.stage_entered is None or self.stage_release is None:
            raise AssertionError("blocking stage requires synchronization events")
        self.stage_entered.set()
        await self.stage_release.wait()

    @staticmethod
    def correlation_id_factory() -> str:
        return _CORRELATION_ID

    def dependencies(self) -> FoundationDependencies:
        return FoundationDependencies(
            configuration_loader=self.configuration_loader,
            logger_factory=self.logger_factory,
            mongo_client_factory=self.mongo_client_factory,
            mongo_connection_verifier=self.mongo_connection_verifier,
            mongo_collection_factory=self.mongo_collection_factory,
            mongo_index_initializer=self.mongo_index_initializer,
            post_repository_factory=self.post_repository_factory,
            mongo_client_closer=self.mongo_client_closer,
            correlation_id_factory=self.correlation_id_factory,
        )


@dataclass(slots=True)
class _BinaryBuffer:
    chunks: list[bytes] = field(default_factory=list)
    flush_count: int = 0

    def write(self, data: bytes, /) -> int:
        self.chunks.append(data)
        return len(data)

    def flush(self) -> None:
        self.flush_count += 1

    def getvalue(self) -> bytes:
        return b"".join(self.chunks)


@dataclass(slots=True)
class _CliApplication:
    startup_error: FoundationConfigurationError | FoundationInfrastructureError | None
    starts: list[tuple[Path, Mapping[str, str]]] = field(default_factory=list)
    shutdown_count: int = 0

    async def start(
        self,
        configuration_path: Path,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> Self:
        assert environ is not None
        self.starts.append((configuration_path, environ))
        if self.startup_error is not None:
            raise self.startup_error
        return self

    async def shutdown(self) -> None:
        self.shutdown_count += 1


def _application(harness: _FoundationHarness) -> FoundationApplication:
    return FoundationApplication(harness.dependencies())


def _event_names(harness: _FoundationHarness) -> list[str]:
    return [cast("str", event["event_name"]) for event in harness.events]


def _assert_readiness(
    application: FoundationApplication,
    *,
    expected: bool,
) -> None:
    assert application.is_ready is expected


@pytest.mark.parametrize(
    ("cli_path", "environment_path", "expected"),
    [
        (
            "cli/configuration.json",
            "environment/configuration.json",
            "cli/configuration.json",
        ),
        (None, "environment/configuration.json", "environment/configuration.json"),
        (None, None, str(cli_module.DEFAULT_CONFIGURATION_PATH)),
    ],
)
def test_configuration_path_precedence_is_cli_then_environment_then_default(
    cli_path: str | None,
    environment_path: str | None,
    expected: str,
) -> None:
    environment = (
        {}
        if environment_path is None
        else {cli_module.CONFIG_PATH_ENVIRONMENT_VARIABLE: environment_path}
    )

    resolved = cli_module.resolve_configuration_path(cli_path, environ=environment)

    assert resolved == Path(expected)


@pytest.mark.parametrize(
    ("cli_path", "environment"),
    [
        ("", {}),
        ("   ", {}),
        (None, {cli_module.CONFIG_PATH_ENVIRONMENT_VARIABLE: ""}),
        (None, {cli_module.CONFIG_PATH_ENVIRONMENT_VARIABLE: "\t  "}),
    ],
)
def test_blank_configuration_paths_are_safe_configuration_failures(
    cli_path: str | None,
    environment: dict[str, str],
) -> None:
    with pytest.raises(FoundationConfigurationError):
        cli_module.resolve_configuration_path(cli_path, environ=environment)


@pytest.mark.parametrize(
    "sensitive_option",
    ["--mongodb-uri", "--password", "--token", "--api-key"],
)
def test_cli_rejects_sensitive_arguments_without_echoing_values(
    sensitive_option: str,
) -> None:
    secret = "command-line-" + "private-value"
    output = _BinaryBuffer()

    result = cli_module.main(
        [sensitive_option, secret],
        environ={},
        output=output,
    )

    rendered = output.getvalue().decode("utf-8")
    assert result == FoundationExitCode.CONFIGURATION_ERROR
    assert secret not in rendered
    assert sensitive_option not in rendered
    assert _event_names_from_json_lines(rendered) == [
        "startup_begun",
        "configuration_validation_failed",
        "startup_failed",
    ]


def _event_names_from_json_lines(rendered: str) -> list[str]:
    import json

    events = [json.loads(line) for line in rendered.splitlines()]
    return [cast("str", event["event_name"]) for event in events]


def test_startup_wires_foundation_in_fail_fast_order_and_exposes_repository() -> None:
    harness = _FoundationHarness()
    application = _application(harness)
    configuration_path = Path("chosen/configuration.json")
    environment = {"SAFE_SETTING": "value"}

    started = _run(application.start(configuration_path, environ=environment))

    assert started is application
    _assert_readiness(application, expected=True)
    assert application.repository is harness.repository_value
    assert application.correlation_id == _CORRELATION_ID
    assert harness.requested_path == configuration_path
    assert harness.requested_environ is environment
    assert harness.order == [
        "configuration",
        "logger:INFO",
        "logger:DEBUG",
        "client",
        "ping",
        "collection",
        "indexes",
        "repository",
    ]

    _run(application.shutdown())
    _assert_readiness(application, expected=False)
    with pytest.raises(FoundationLifecycleError):
        _ = application.repository


def test_readiness_waits_for_both_ping_and_index_initialization() -> None:
    async def scenario() -> None:
        harness = _FoundationHarness()
        application = _application(harness)
        ping_entered = asyncio.Event()
        ping_release = asyncio.Event()
        harness.block_at = "ping"
        harness.stage_entered = ping_entered
        harness.stage_release = ping_release

        startup = asyncio.create_task(application.start(Path("configuration.json")))
        await ping_entered.wait()
        _assert_readiness(application, expected=False)

        index_entered = asyncio.Event()
        index_release = asyncio.Event()
        harness.block_at = "index"
        harness.stage_entered = index_entered
        harness.stage_release = index_release
        ping_release.set()
        await index_entered.wait()
        _assert_readiness(application, expected=False)

        index_release.set()
        await startup
        _assert_readiness(application, expected=True)
        await application.shutdown()

    _run(scenario())


def test_shutdown_is_rejected_while_startup_owns_the_lifecycle() -> None:
    async def scenario() -> None:
        harness = _FoundationHarness(block_at="ping")
        harness.stage_entered = asyncio.Event()
        harness.stage_release = asyncio.Event()
        application = _application(harness)
        startup = asyncio.create_task(application.start(Path("configuration.json")))
        await harness.stage_entered.wait()

        with pytest.raises(FoundationLifecycleError):
            await application.shutdown()
        assert harness.close_count == 0

        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup
        assert harness.close_count == 1

    _run(scenario())


def test_configuration_failure_is_reported_before_any_mongodb_construction() -> None:
    issue = ConfigurationIssue(
        path=("mongodb", "uri"),
        message="required environment variable is missing: TAB_MONGODB_URI",
        code="missing_secret",
    )
    harness = _FoundationHarness(
        configuration_error=ConfigurationValidationError([issue])
    )
    application = _application(harness)

    with pytest.raises(FoundationConfigurationError) as captured:
        _run(application.start(Path("invalid-configuration.json"), environ={}))

    assert isinstance(captured.value.__cause__, ConfigurationValidationError)
    _assert_readiness(application, expected=False)
    assert harness.close_count == 0
    assert harness.order == ["configuration", "logger:DEBUG"]
    assert _event_names(harness) == [
        "startup_begun",
        "configuration_validation_failed",
        "startup_failed",
    ]
    assert all(event["correlation_id"] == _CORRELATION_ID for event in harness.events)


def test_first_audit_sink_failure_is_safe_and_prevents_external_startup() -> None:
    harness = _FoundationHarness(sink_failure_at_call=1)
    application = _application(harness)

    with pytest.raises(FoundationInfrastructureError) as captured:
        _run(application.start(Path("configuration.json")))

    assert isinstance(captured.value.__cause__, RuntimeError)
    _assert_readiness(application, expected=False)
    assert "client" not in harness.order
    assert harness.close_count == 0
    assert _event_names(harness) == ["startup_failed"]


def test_logger_or_client_factory_failure_does_not_close_an_unowned_resource() -> None:
    for failure_at, expected_order in (
        ("logger", ["configuration", "logger:INFO"]),
        (
            "client",
            ["configuration", "logger:INFO", "logger:DEBUG", "client"],
        ),
    ):
        harness = _FoundationHarness(failure_at=cast("FailureStage", failure_at))
        application = _application(harness)

        with pytest.raises(FoundationInfrastructureError):
            _run(application.start(Path("configuration.json")))

        _assert_readiness(application, expected=False)
        assert harness.close_count == 0
        assert harness.order == expected_order


@pytest.mark.parametrize(
    ("failure_at", "expected_prefix"),
    [
        (
            "ping",
            ["configuration", "logger:INFO", "logger:DEBUG", "client", "ping"],
        ),
        (
            "index",
            [
                "configuration",
                "logger:INFO",
                "logger:DEBUG",
                "client",
                "ping",
                "collection",
                "indexes",
            ],
        ),
    ],
)
def test_ping_and_index_failures_close_the_owned_client_once(
    failure_at: Literal["ping", "index"],
    expected_prefix: list[str],
) -> None:
    harness = _FoundationHarness(failure_at=failure_at)
    application = _application(harness)

    with pytest.raises(FoundationInfrastructureError) as captured:
        _run(application.start(Path("configuration.json")))

    assert isinstance(captured.value.__cause__, RuntimeError)
    _assert_readiness(application, expected=False)
    assert harness.close_count == 1
    assert harness.order == [*expected_prefix, "close"]
    assert "repository" not in harness.order
    assert _event_names(harness)[-4:] == [
        "startup_failed",
        "shutdown_begun",
        "resource_closed",
        "shutdown_completed",
    ]


def test_repeated_and_concurrent_shutdown_closes_the_client_only_once() -> None:
    async def scenario() -> None:
        harness = _FoundationHarness()
        application = _application(harness)
        await application.start(Path("configuration.json"))
        harness.close_entered = asyncio.Event()
        harness.close_release = asyncio.Event()

        first = asyncio.create_task(application.shutdown())
        await harness.close_entered.wait()
        second = asyncio.create_task(application.shutdown())
        await asyncio.sleep(0)
        assert harness.close_count == 1

        harness.close_release.set()
        await asyncio.gather(first, second)
        await application.shutdown()
        assert harness.close_count == 1

    _run(scenario())


@pytest.mark.parametrize("block_at", ["ping", "index"])
def test_cancellation_during_startup_cleans_up_and_propagates(
    block_at: BlockingStage,
) -> None:
    async def scenario() -> None:
        harness = _FoundationHarness(block_at=block_at)
        harness.stage_entered = asyncio.Event()
        harness.stage_release = asyncio.Event()
        application = _application(harness)
        startup = asyncio.create_task(application.start(Path("configuration.json")))
        await harness.stage_entered.wait()

        startup.cancel()
        with pytest.raises(asyncio.CancelledError):
            await startup

        _assert_readiness(application, expected=False)
        assert harness.close_count == 1
        assert harness.order[-1] == "close"
        assert "startup_cancelled" in _event_names(harness)
        assert _event_names(harness)[-3:] == [
            "shutdown_begun",
            "resource_closed",
            "shutdown_completed",
        ]

    _run(scenario())


def test_second_cancellation_waits_for_started_cleanup_before_propagating() -> None:
    async def scenario() -> None:
        harness = _FoundationHarness(block_at="ping")
        harness.stage_entered = asyncio.Event()
        harness.stage_release = asyncio.Event()
        harness.close_entered = asyncio.Event()
        harness.close_release = asyncio.Event()
        application = _application(harness)
        startup = asyncio.create_task(application.start(Path("configuration.json")))
        await harness.stage_entered.wait()

        startup.cancel()
        await harness.close_entered.wait()
        startup.cancel()
        await asyncio.sleep(0)
        assert startup.done() is False

        harness.close_release.set()
        with pytest.raises(asyncio.CancelledError):
            await startup
        assert harness.close_count == 1
        assert _event_names(harness)[-3:] == [
            "shutdown_begun",
            "resource_closed",
            "shutdown_completed",
        ]

    _run(scenario())


def test_cancelled_shutdown_joins_the_single_close_before_propagating() -> None:
    async def scenario() -> None:
        harness = _FoundationHarness()
        application = _application(harness)
        await application.start(Path("configuration.json"))
        harness.close_entered = asyncio.Event()
        harness.close_release = asyncio.Event()
        shutdown = asyncio.create_task(application.shutdown())
        await harness.close_entered.wait()

        shutdown.cancel()
        await asyncio.sleep(0)
        assert shutdown.done() is False
        harness.close_release.set()

        with pytest.raises(asyncio.CancelledError):
            await shutdown
        assert harness.close_count == 1
        _assert_readiness(application, expected=False)

    _run(scenario())


def test_successful_event_order_is_deterministic_and_fully_correlated() -> None:
    harness = _FoundationHarness()
    application = _application(harness)

    _run(application.start(Path("configuration.json")))
    _run(application.shutdown())

    assert _event_names(harness) == [
        "startup_begun",
        "configuration_validation_succeeded",
        "logging_initialized",
        "mongodb_connected",
        "indexes_ready",
        "application_ready",
        "shutdown_begun",
        "resource_closed",
        "shutdown_completed",
    ]
    assert all(event["correlation_id"] == _CORRELATION_ID for event in harness.events)
    assert all(
        event["timestamp"] == _FIXED_TIME.isoformat(timespec="microseconds")
        for event in harness.events
    )


def test_shutdown_audit_preserves_an_explicit_safe_runtime_reason() -> None:
    harness = _FoundationHarness()
    application = _application(harness)

    _run(application.start(Path("configuration.json")))
    _run(application.shutdown(reason="critical_task_failed"))

    event = next(
        item for item in harness.events if item["event_name"] == "shutdown_begun"
    )
    assert event["reason"] == "critical_task_failed"


def test_high_application_log_level_does_not_suppress_lifecycle_audit_events() -> None:
    loaded = _loaded_configuration()
    critical_settings = loaded.settings.model_copy(
        update={"logging": LoggingConfig(level=LogLevel.CRITICAL)}
    )
    harness = _FoundationHarness(
        loaded=LoadedConfiguration(
            settings=critical_settings,
            secrets=loaded.secrets,
        )
    )
    application = _application(harness)

    _run(application.start(Path("configuration.json")))

    assert harness.logger_levels == [LogLevel.CRITICAL, LogLevel.DEBUG]
    assert application.logger is not None
    assert _event_names(harness) == [
        "startup_begun",
        "configuration_validation_succeeded",
        "logging_initialized",
        "mongodb_connected",
        "indexes_ready",
        "application_ready",
    ]
    _run(application.shutdown())


def test_persian_failure_context_survives_credential_uri_redaction() -> None:
    username = "synthetic" + "-username"
    password = "synthetic" + "-password"
    uri = f"mongodb://{username}:{password}@127.0.0.1:27017/private"
    environment = {**_SYNTHETIC_ENVIRONMENT, "TAB_MONGODB_URI": uri}
    message = f"خطای فارسی پیش از {username} و {password} در {uri} و پس از آن ✨"
    harness = _FoundationHarness(
        failure_at="ping",
        operational_error=RuntimeError(message),
        loaded=_loaded_configuration(environment),
    )
    application = _application(harness)

    with pytest.raises(FoundationInfrastructureError):
        _run(application.start(Path("configuration.json")))

    rendered = repr(harness.events)
    failure_event = next(
        event for event in harness.events if event["event_name"] == "startup_failed"
    )
    assert failure_event["error_message"] == (
        "خطای فارسی پیش از [REDACTED] و [REDACTED] در [REDACTED] و پس از آن ✨"
    )
    assert all(username in values for values in harness.logger_secret_values)
    assert all(password in values for values in harness.logger_secret_values)
    assert username not in rendered
    assert password not in rendered
    assert uri not in rendered


@pytest.mark.parametrize(
    ("failure_kind", "expected_code", "expected_shutdowns"),
    [
        (None, FoundationExitCode.SUCCESS, 1),
        ("configuration", FoundationExitCode.CONFIGURATION_ERROR, 0),
        ("infrastructure", FoundationExitCode.INFRASTRUCTURE_ERROR, 0),
    ],
)
def test_cli_returns_stable_exit_codes_with_an_injected_application_factory(
    monkeypatch: pytest.MonkeyPatch,
    failure_kind: Literal["configuration", "infrastructure"] | None,
    expected_code: FoundationExitCode,
    expected_shutdowns: int,
) -> None:
    startup_error: FoundationConfigurationError | FoundationInfrastructureError | None
    if failure_kind == "configuration":
        startup_error = FoundationConfigurationError()
    elif failure_kind == "infrastructure":
        startup_error = FoundationInfrastructureError()
    else:
        startup_error = None
    fake = _CliApplication(startup_error=startup_error)

    def factory(*, sink: object) -> FoundationApplication:
        del sink
        return cast("FoundationApplication", fake)

    monkeypatch.setattr(cli_module, "create_foundation_application", factory)
    environment = {
        cli_module.CONFIG_PATH_ENVIRONMENT_VARIABLE: "environment.json",
        "SAFE_VALUE": "preserved",
    }

    result = cli_module.main(
        ["--config", "command-line.json"],
        environ=environment,
        output=_BinaryBuffer(),
    )

    assert result == expected_code
    assert fake.shutdown_count == expected_shutdowns
    assert len(fake.starts) == 1
    configuration_path, received_environment = fake.starts[0]
    assert configuration_path == Path("command-line.json")
    assert received_environment == environment
    assert received_environment is not environment


@pytest.mark.parametrize(
    ("command", "expected_code"),
    [
        ("login", FoundationExitCode.SUCCESS),
        ("ingest", FoundationExitCode.INFRASTRUCTURE_ERROR),
        ("ingest-text", FoundationExitCode.INFRASTRUCTURE_ERROR),
    ],
)
def test_cli_dispatches_explicit_telegram_commands(
    monkeypatch: pytest.MonkeyPatch,
    command: Literal["login", "ingest", "ingest-text"],
    expected_code: FoundationExitCode,
) -> None:
    calls: list[tuple[str, Path]] = []
    ingestion_application = object()

    async def login(path: Path, **_kwargs: object) -> FoundationExitCode:
        calls.append(("login", path))
        return FoundationExitCode.SUCCESS

    async def ingest(
        application: object,
        path: Path,
        **_kwargs: object,
    ) -> FoundationExitCode:
        assert application is ingestion_application
        calls.append((command, path))
        return FoundationExitCode.INFRASTRUCTURE_ERROR

    monkeypatch.setattr(cli_module, "run_telegram_login", login)
    monkeypatch.setattr(
        cli_module,
        "create_text_ingestion_application",
        lambda *, sink: ingestion_application,
    )
    monkeypatch.setattr(cli_module, "run_text_ingestion_application", ingest)

    result = cli_module.main(
        [command, "--config", "telegram.json"],
        environ={},
        output=_BinaryBuffer(),
    )

    assert result == expected_code
    assert calls == [(command, Path("telegram.json"))]


def test_cli_dispatches_one_shot_media_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    async def cleanup(path: Path, **_kwargs: object) -> FoundationExitCode:
        calls.append(path)
        return FoundationExitCode.SUCCESS

    monkeypatch.setattr(cli_module, "run_media_cleanup", cleanup)

    result = cli_module.main(
        ["media-cleanup", "--config", "media.json"],
        environ={},
        output=_BinaryBuffer(),
    )

    assert result == FoundationExitCode.SUCCESS
    assert calls == [Path("media.json")]


def test_cli_inspects_queue_without_starting_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[tuple[Path, str]] = []

    async def inspect_queue(
        path: Path, *, status: str, **_kwargs: object
    ) -> tuple[str, ...]:
        calls.append((path, status))
        return (
            "job_id=job-1 | post_id=short | source_message_id=42 | "
            "destination=dest | action=immediate | status=Pending | "
            "due_at=2026-07-13T12:00:00+00:00 | attempt_count=0",
        )

    monkeypatch.setattr(cli_module, "inspect_publication_queue", inspect_queue)
    monkeypatch.setattr(
        cli_module,
        "create_operational_runtime_application",
        lambda **_kwargs: pytest.fail("queue inspection started runtime"),
    )

    result = cli_module.main(
        [
            "publication-queue",
            "--config",
            "queue.json",
            "--status",
            "pending",
        ],
        environ={},
        output=_BinaryBuffer(),
    )

    assert result == FoundationExitCode.SUCCESS
    assert calls == [(Path("queue.json"), "pending")]
    output = capsys.readouterr().out
    assert "job_id=job-1" in output
    assert "متن" not in output


def test_cli_cancellation_requires_job_id_and_is_explicitly_dispatched(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from telegram_assist_bot.domain import CancellationResult

    calls: list[tuple[Path, str]] = []

    async def cancel(
        path: Path, *, job_id: str, **_kwargs: object
    ) -> CancellationResult:
        calls.append((path, job_id))
        return CancellationResult.ALREADY_CANCELLED

    monkeypatch.setattr(cli_module, "cancel_publication_job", cancel)
    assert (
        cli_module.main(
            ["publication-cancel", "--config", "queue.json"],
            environ={},
            output=_BinaryBuffer(),
        )
        == FoundationExitCode.CONFIGURATION_ERROR
    )
    result = cli_module.main(
        [
            "publication-cancel",
            "--config",
            "queue.json",
            "--job-id",
            "job-1",
        ],
        environ={},
        output=_BinaryBuffer(),
    )
    assert result == FoundationExitCode.SUCCESS
    assert calls == [(Path("queue.json"), "job-1")]
    assert "AlreadyCancelled" in capsys.readouterr().out


def test_cli_presend_recovery_requires_exact_post_id_and_dispatches(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from telegram_assist_bot.bootstrap.publication_queue import PreSendRecoveryResult

    calls: list[tuple[Path, str]] = []

    async def recover(
        path: Path, *, approval_post_id: str, **_kwargs: object
    ) -> PreSendRecoveryResult:
        calls.append((path, approval_post_id))
        return PreSendRecoveryResult.REQUEUED

    monkeypatch.setattr(cli_module, "recover_pre_send_publication", recover)
    assert (
        cli_module.main(
            ["publication-recover-presend", "--config", "queue.json"],
            environ={},
            output=_BinaryBuffer(),
        )
        == FoundationExitCode.CONFIGURATION_ERROR
    )

    result = cli_module.main(
        [
            "publication-recover-presend",
            "--config",
            "queue.json",
            "--approval-post-id",
            "post-1",
        ],
        environ={},
        output=_BinaryBuffer(),
    )

    assert result == FoundationExitCode.SUCCESS
    assert calls == [(Path("queue.json"), "post-1")]
    assert "recovery_result=requeued" in capsys.readouterr().out


def test_cli_failed_immediate_recovery_forwards_dry_run_and_requeue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from telegram_assist_bot.bootstrap.publication_queue import PreSendRecoveryResult

    calls: list[tuple[Path, str, bool, bool]] = []

    async def recover(
        path: Path,
        *,
        approval_post_id: str,
        dry_run: bool,
        requeue: bool,
        **_kwargs: object,
    ) -> PreSendRecoveryResult:
        calls.append((path, approval_post_id, dry_run, requeue))
        return PreSendRecoveryResult.DRY_RUN_ELIGIBLE

    monkeypatch.setattr(cli_module, "recover_failed_immediate_selection", recover)
    result = cli_module.main(
        [
            "publication-recover-immediate",
            "--config",
            "queue.json",
            "--approval-post-id",
            "post-1",
            "--dry-run",
            "--requeue",
        ],
        environ={},
        output=_BinaryBuffer(),
    )
    assert result == FoundationExitCode.SUCCESS
    assert calls == [(Path("queue.json"), "post-1", True, True)]
    assert "recovery_result=dry_run_eligible" in capsys.readouterr().out


def test_cli_inspects_and_explicitly_retries_approval_queue(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    inspected: list[tuple[Path, str]] = []
    retried: list[tuple[Path, str]] = []

    async def inspect(path: Path, *, status: str, **_kwargs: object) -> tuple[str, ...]:
        inspected.append((path, status))
        return ("approval_post_id=short | content_kind=album | status=retry",)

    async def retry(path: Path, *, approval_post_id: str, **_kwargs: object) -> bool:
        retried.append((path, approval_post_id))
        return True

    monkeypatch.setattr(cli_module, "inspect_approval_queue", inspect)
    monkeypatch.setattr(cli_module, "retry_approval_delivery", retry)
    assert (
        cli_module.main(
            ["approval-queue", "--config", "queue.json", "--status", "retry"],
            environ={},
            output=_BinaryBuffer(),
        )
        == FoundationExitCode.SUCCESS
    )
    assert (
        cli_module.main(
            [
                "approval-retry",
                "--config",
                "queue.json",
                "--approval-post-id",
                "proposal-exact",
            ],
            environ={},
            output=_BinaryBuffer(),
        )
        == FoundationExitCode.SUCCESS
    )
    assert inspected == [(Path("queue.json"), "retry")]
    assert retried == [(Path("queue.json"), "proposal-exact")]
    output = capsys.readouterr().out
    assert "approval_post_id=short" in output
    assert "approval_retry_queued=true" in output


def test_cli_document_recovery_requires_bounded_selector_and_supports_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[dict[str, object]] = []

    async def recover(_path: Path, **kwargs: object) -> object:
        calls.append(kwargs)
        return SimpleNamespace(
            matching_post_ids=("proposal-identity-long",),
            requeued_post_ids=(),
        )

    monkeypatch.setattr(cli_module, "recover_rejected_document_deliveries", recover)
    assert (
        cli_module.main(
            ["approval-recover-documents", "--config", "queue.json"],
            environ={},
            output=_BinaryBuffer(),
        )
        == FoundationExitCode.CONFIGURATION_ERROR
    )
    assert (
        cli_module.main(
            [
                "approval-recover-documents",
                "--config",
                "queue.json",
                "--approval-post-id",
                "proposal-identity-long",
                "--dry-run",
                "--limit",
                "5",
            ],
            environ={},
            output=_BinaryBuffer(),
        )
        == FoundationExitCode.SUCCESS
    )
    assert calls[0]["approval_post_id"] == "proposal-identity-long"
    assert calls[0]["dry_run"] is True
    assert calls[0]["limit"] == 5
    output = capsys.readouterr().out
    assert "approval_document_recovery_mode=dry-run" in output
    assert "approval_post_id=proposal-ide" in output
    assert "proposal-identity-long" not in output


def test_cli_recovery_time_parser_requires_an_aware_iso_value() -> None:
    assert cli_module._aware_datetime("2026-07-16T12:30:00Z").tzinfo is not None
    with pytest.raises(argparse.ArgumentTypeError, match="aware ISO-8601"):
        cli_module._aware_datetime("not-a-time")
    with pytest.raises(argparse.ArgumentTypeError, match="aware ISO-8601"):
        cli_module._aware_datetime("2026-07-16T12:30:00")


def test_import_and_reload_do_not_execute_startup_or_open_resources(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def reject_startup(*_args: object, **_kwargs: object) -> None:
        calls.append("startup")
        raise AssertionError("module import executed startup")

    with monkeypatch.context() as patcher:
        patcher.setattr(cli_module, "main", reject_startup)
        importlib.reload(bootstrap_package)
        importlib.reload(entry_point_module)
        assert calls == []

    importlib.reload(bootstrap_package)
    importlib.reload(entry_point_module)


def test_composition_root_ast_has_no_later_product_dependencies() -> None:
    source_files = (
        _REPOSITORY_ROOT / "src" / "telegram_assist_bot" / "bootstrap" / "runtime.py",
        _REPOSITORY_ROOT / "src" / "telegram_assist_bot" / "bootstrap" / "cli.py",
        _REPOSITORY_ROOT / "src" / "telegram_assist_bot" / "__main__.py",
    )
    forbidden_prefixes = (
        "telegram",
        "telethon",
        "pyrogram",
        "openai",
        "anthropic",
        "google.generativeai",
        "apscheduler",
        "telegram_assist_bot.workers",
        "telegram_assist_bot.infrastructure.telegram",
        "telegram_assist_bot.infrastructure.ai",
        "telegram_assist_bot.infrastructure.media",
        "telegram_assist_bot.infrastructure.scheduler",
    )
    violations: list[str] = []

    for source_file in source_files:
        tree = ast.parse(
            source_file.read_text(encoding="utf-8"), filename=source_file.name
        )
        for node in ast.walk(tree):
            imported_names: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported_names = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                imported_names = tuple(
                    f"{node.module}.{alias.name}" for alias in node.names
                )
            violations.extend(
                f"{source_file.name}: {imported_name}"
                for imported_name in imported_names
                if any(
                    imported_name == prefix or imported_name.startswith(f"{prefix}.")
                    for prefix in forbidden_prefixes
                )
            )

    assert violations == []
