"""Safe command-line boundary for the one-shot foundation startup check."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Never
from uuid import uuid4

from telegram_assist_bot.bootstrap.runtime import (
    BinaryEventStream,
    FoundationApplication,
    FoundationConfigurationError,
    FoundationExitCode,
    FoundationStartupError,
    JsonLineEventSink,
    create_foundation_application,
)
from telegram_assist_bot.bootstrap.telegram_login import run_telegram_login
from telegram_assist_bot.bootstrap.text_ingestion import (
    create_text_ingestion_application,
    run_text_ingestion_application,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    Redactor,
    StructuredLogger,
    bind_log_context,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

CONFIG_PATH_ENVIRONMENT_VARIABLE = "TAB_CONFIG_PATH"
"""Environment variable used for the non-secret configuration file path."""

DEFAULT_CONFIGURATION_PATH = Path("config/configuration.json")
"""Working-directory-relative configuration path used as the final fallback."""


class CliUsageError(ValueError):
    """Report invalid command-line input without retaining raw arguments."""

    error_category = "configuration"

    def __init__(self) -> None:
        """Initialize a fixed safe message."""
        super().__init__("Command-line arguments are invalid.")


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> Never:
        del message
        raise CliUsageError


def resolve_configuration_path(
    cli_path: str | None,
    *,
    environ: Mapping[str, str],
) -> Path:
    """Resolve CLI, environment, then default configuration-path precedence."""
    raw_path = (
        cli_path
        if cli_path is not None
        else environ.get(CONFIG_PATH_ENVIRONMENT_VARIABLE)
    )
    if raw_path is None:
        return DEFAULT_CONFIGURATION_PATH
    if not raw_path or raw_path.isspace():
        raise FoundationConfigurationError
    return Path(raw_path)


async def run_foundation_application(
    application: FoundationApplication,
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
) -> FoundationExitCode:
    """Start and immediately stop the worker-free foundation lifecycle."""
    try:
        await application.start(configuration_path, environ=environ)
        await application.shutdown()
    except asyncio.CancelledError as cancellation:
        try:
            await application.shutdown()
        except asyncio.CancelledError:
            cancellation.add_note("Additional cancellation arrived during shutdown.")
        except FoundationStartupError:
            cancellation.add_note("Foundation shutdown failed safely.")
        raise
    except FoundationStartupError as error:
        return error.exit_code
    return FoundationExitCode.SUCCESS


def _parser() -> _SafeArgumentParser:
    parser = _SafeArgumentParser(
        prog="python -m telegram_assist_bot",
        description=("Run the foundation check or explicit Telegram session login."),
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=("check", "login", "ingest-text"),
        default="check",
        help=(
            "Use 'login' for explicit authentication or 'ingest-text' for the "
            "Milestone 1 worker."
        ),
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        help=(
            "Non-secret configuration path; overrides TAB_CONFIG_PATH and the "
            "default config/configuration.json path."
        ),
    )
    return parser


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _report_cli_failure(
    *,
    sink: JsonLineEventSink,
    redactor: Redactor,
    error: BaseException,
) -> None:
    logger = StructuredLogger(
        sink=sink,
        clock=_utc_now,
        redactor=redactor,
        minimum_level=LogLevel.DEBUG,
    )
    context = CorrelationContext(correlation_id=uuid4().hex)
    with bind_log_context(context):
        logger.emit(level=LogLevel.INFO, event_name="startup_begun")
        logger.emit(
            level=LogLevel.ERROR,
            event_name="configuration_validation_failed",
            error=error,
        )
        logger.emit(
            level=LogLevel.ERROR,
            event_name="startup_failed",
            error=error,
        )


def main(
    argv: Sequence[str] | None = None,
    *,
    environ: Mapping[str, str] | None = None,
    output: BinaryEventStream | None = None,
) -> int:
    """Run the safe one-shot foundation CLI and return its stable exit code."""
    environment_snapshot = dict(os.environ if environ is None else environ)
    stream = sys.stderr.buffer if output is None else output
    redactor = Redactor()
    sink = JsonLineEventSink(stream, redactor=redactor)

    try:
        arguments = _parser().parse_args(argv)
        configuration_path = resolve_configuration_path(
            arguments.config,
            environ=environment_snapshot,
        )
    except (CliUsageError, FoundationConfigurationError) as error:
        with suppress(Exception):
            _report_cli_failure(sink=sink, redactor=redactor, error=error)
        return int(FoundationExitCode.CONFIGURATION_ERROR)

    if arguments.command == "login":
        exit_code = asyncio.run(
            run_telegram_login(
                configuration_path,
                environ=environment_snapshot,
                sink=sink,
            )
        )
    elif arguments.command == "ingest-text":
        ingestion_application = create_text_ingestion_application(sink=sink)
        exit_code = asyncio.run(
            run_text_ingestion_application(
                ingestion_application,
                configuration_path,
                environ=environment_snapshot,
            )
        )
    else:
        foundation_application = create_foundation_application(sink=sink)
        exit_code = asyncio.run(
            run_foundation_application(
                foundation_application,
                configuration_path,
                environ=environment_snapshot,
            )
        )
    return int(exit_code)


__all__ = (
    "CONFIG_PATH_ENVIRONMENT_VARIABLE",
    "DEFAULT_CONFIGURATION_PATH",
    "CliUsageError",
    "main",
    "resolve_configuration_path",
    "run_foundation_application",
)
