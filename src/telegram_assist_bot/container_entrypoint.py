"""Container entrypoint that enforces the bounded restart contract.

Docker restart policies cannot inspect exception classes or exit-code
classification on their own. The application therefore owns stable exit codes and
this module translates them into the container-visible contract:

- A non-retryable startup failure exits the container cleanly so a bounded
  ``on-failure`` restart policy cannot restart it forever.
- Every other non-zero code is passed through unchanged, so transient
  infrastructure failures keep their bounded restart and recovery behavior.

The terminal stop is recorded with one CRITICAL structured event that never
contains secret material.
"""

from __future__ import annotations

import sys
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from telegram_assist_bot.bootstrap import main
from telegram_assist_bot.bootstrap.runtime import (
    FoundationExitCode,
    JsonLineEventSink,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    CorrelationContext,
    Redactor,
    StructuredLogger,
    bind_log_context,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

TERMINAL_EXIT_CODES = frozenset({FoundationExitCode.PERMANENT_STARTUP_FAILURE})
"""Exit codes that must stop the container instead of restarting it."""

TERMINAL_EVENT_NAME = "container_terminal_startup_failure"
"""Event recorded once when a terminal startup failure stops the container."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def container_exit_code(application_exit_code: int) -> int:
    """Return the container exit code for one application exit code.

    A terminal startup failure becomes a clean stop so that the configured
    ``on-failure`` restart policy leaves the container stopped instead of
    restarting it forever. Unknown non-zero codes stay unchanged and therefore
    remain restartable.
    """
    try:
        code = FoundationExitCode(application_exit_code)
    except ValueError:
        return application_exit_code
    if code in TERMINAL_EXIT_CODES:
        return int(FoundationExitCode.SUCCESS)
    return int(code)


def report_terminal_startup_failure(exit_code: FoundationExitCode) -> None:
    """Emit one CRITICAL structured event for a deliberate container stop."""
    redactor = Redactor()
    logger = StructuredLogger(
        sink=JsonLineEventSink(sys.stderr.buffer, redactor=redactor),
        clock=_utc_now,
        redactor=redactor,
        minimum_level=LogLevel.DEBUG,
    )
    with bind_log_context(CorrelationContext(correlation_id=uuid4().hex)):
        logger.emit(
            level=LogLevel.CRITICAL,
            event_name=TERMINAL_EVENT_NAME,
            fields={
                "application_exit_code": int(exit_code),
                "restart_policy_result": "stopped",
            },
        )


def container_main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[[Sequence[str] | None], int] = main,
    reporter: Callable[[FoundationExitCode], None] = report_terminal_startup_failure,
) -> int:
    """Run the CLI and translate terminal startup failures into a clean stop."""
    exit_code = runner(argv)
    try:
        code = FoundationExitCode(exit_code)
    except ValueError:
        return exit_code
    if code in TERMINAL_EXIT_CODES:
        with suppress(Exception):
            reporter(code)
    return container_exit_code(exit_code)


if __name__ == "__main__":  # pragma: no cover - exercised through the image.
    raise SystemExit(container_main())


__all__ = (
    "TERMINAL_EVENT_NAME",
    "TERMINAL_EXIT_CODES",
    "container_exit_code",
    "container_main",
    "report_terminal_startup_failure",
)
