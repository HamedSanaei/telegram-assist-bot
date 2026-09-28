"""Unit proofs for the classified startup failure and process contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import (
    TelegramChannelValidationError,
    TelegramChannelValidationIssue,
    TelegramPremiumRequiredError,
)
from telegram_assist_bot.bootstrap.runtime import (
    FoundationConfigurationError,
    FoundationExitCode,
    FoundationInfrastructureError,
    classify_startup_failure,
    emit_startup_failure,
    startup_exit_code,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import (
    AuthorizationError,
    StartupFailureClass,
    TransientOperationError,
)
from telegram_assist_bot.shared.observability import Redactor, StructuredLogger

if TYPE_CHECKING:
    from collections.abc import Mapping

    from telegram_assist_bot.shared.observability import RedactedValue

_NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class RecordingSink:
    """Capture redacted structured events for assertions."""

    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def __call__(self, event: Mapping[str, RedactedValue]) -> None:
        self.events.append(dict(event))


def _logger(*, secrets: tuple[str, ...] = ()) -> tuple[StructuredLogger, RecordingSink]:
    sink = RecordingSink()
    logger = StructuredLogger(
        sink=sink,
        clock=lambda: _NOW,
        redactor=Redactor(secret_values=secrets),
        minimum_level=LogLevel.DEBUG,
    )
    return logger, sink


@pytest.mark.parametrize(
    "error",
    [
        TelegramPremiumRequiredError(),
        TelegramChannelValidationError(
            (TelegramChannelValidationIssue("source_channels.0", "code", "permission"),)
        ),
        AuthorizationError(),
    ],
)
def test_non_retryable_startup_failures_are_permanent(error: BaseException) -> None:
    assert classify_startup_failure(error) is StartupFailureClass.PERMANENT
    assert startup_exit_code(StartupFailureClass.PERMANENT) is (
        FoundationExitCode.PERMANENT_STARTUP_FAILURE
    )


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("synthetic"),
        TimeoutError(),
        TransientOperationError(),
        FoundationInfrastructureError(),
        ConnectionResetError(),
    ],
)
def test_retryable_and_unknown_startup_failures_stay_transient(
    error: BaseException,
) -> None:
    assert classify_startup_failure(error) is StartupFailureClass.TRANSIENT
    assert startup_exit_code(StartupFailureClass.TRANSIENT) is (
        FoundationExitCode.INFRASTRUCTURE_ERROR
    )


def test_foundation_configuration_failure_is_permanent_configuration() -> None:
    assert (
        classify_startup_failure(FoundationConfigurationError())
        is StartupFailureClass.PERMANENT
    )


class SyntheticAuthorizationError(RuntimeError):
    """Carry a non-retryable category and an unsafe message for redaction."""

    error_category = "authorization"


def test_permanent_events_are_fatal_single_and_secret_free() -> None:
    logger, sink = _logger(secrets=("supersecret",))

    emit_startup_failure(
        logger,
        failure_class=StartupFailureClass.PERMANENT,
        error=SyntheticAuthorizationError("token supersecret rejected"),
    )

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_name"] == "startup_failed_permanently"
    assert event["level"] == "CRITICAL"
    assert event["failure_class"] == "permanent"
    assert event["failure_type"] == "SyntheticAuthorizationError"
    assert event["failure_category"] == "authorization"
    assert event["exit_code"] == 4
    assert event["error_message"] == "token [REDACTED] rejected"
    assert "supersecret" not in str(event)


def test_transient_events_are_degraded_and_retryable() -> None:
    logger, sink = _logger()
    emit_startup_failure(
        logger,
        failure_class=StartupFailureClass.TRANSIENT,
        error=FoundationInfrastructureError(),
    )
    assert len(sink.events) == 1
    event = sink.events[0]
    assert event["event_name"] == "startup_failed_transient"
    assert event["level"] == "ERROR"
    assert event["failure_class"] == "transient"
    assert event["exit_code"] == 3


def test_exit_codes_keep_their_documented_values() -> None:
    assert int(FoundationExitCode.SUCCESS) == 0
    assert int(FoundationExitCode.CONFIGURATION_ERROR) == 2
    assert int(FoundationExitCode.INFRASTRUCTURE_ERROR) == 3
    assert int(FoundationExitCode.PERMANENT_STARTUP_FAILURE) == 4


def test_declared_permanent_category_is_permanent_without_an_application_base() -> None:
    assert (
        classify_startup_failure(SyntheticAuthorizationError("synthetic"))
        is StartupFailureClass.PERMANENT
    )
