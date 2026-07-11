"""Structured, redacted logging events with injected output and time sources."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Protocol

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import classify_error
from telegram_assist_bot.shared.observability.context import current_log_context
from telegram_assist_bot.shared.observability.redaction import (
    RedactedValue,
    Redactor,
)

type StructuredEvent = Mapping[str, RedactedValue]
type EventClock = Callable[[], datetime]

_BASE_FIELDS = frozenset({"timestamp", "level", "event_name", "correlation_id"})
_CONTEXT_FIELDS = frozenset(
    {
        "task_id",
        "job_id",
        "post_id",
        "channel_id",
        "destination_id",
        "admin_id",
    }
)
_ERROR_FIELDS = frozenset({"error_type", "error_category", "error_message"})
_RESERVED_FIELDS = _BASE_FIELDS | _CONTEXT_FIELDS | _ERROR_FIELDS
_LEVEL_PRIORITY = {
    LogLevel.DEBUG: 10,
    LogLevel.INFO: 20,
    LogLevel.WARNING: 30,
    LogLevel.ERROR: 40,
    LogLevel.CRITICAL: 50,
}


class InvalidLogEventError(ValueError):
    """Report an invalid structured log event without retaining its values."""


class EventSink(Protocol):
    """Consume one already-redacted structured logging event."""

    def __call__(self, event: StructuredEvent) -> None:
        """Handle one immutable-by-contract structured event."""
        ...


def _timestamp_from_clock(clock: EventClock) -> str:
    """Return one canonical UTC timestamp from an injected aware clock."""
    try:
        value = clock()
        if (
            type(value) is not datetime
            or value.tzinfo is None
            or value.utcoffset() is None
        ):
            raise InvalidLogEventError("The logging clock must return an aware time.")
        return value.astimezone(UTC).isoformat(timespec="microseconds")
    except (OverflowError, TypeError, ValueError):
        raise InvalidLogEventError(
            "The logging clock must return a valid aware time."
        ) from None


def format_json_event(event: Mapping[str, object], *, redactor: Redactor) -> str:
    """Serialize one event as strict UTF-8-safe JSON after applying redaction."""
    if not isinstance(event, Mapping):
        raise InvalidLogEventError("A mapping log event is required.")
    redacted = redactor.redact(event)
    if not isinstance(redacted, dict):
        raise InvalidLogEventError("A mapping log event is required.")
    return json.dumps(
        redacted,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


class StructuredLogger:
    """Build allowlisted, task-aware events and emit only redacted values."""

    __slots__ = ("_clock", "_minimum_level", "_redactor", "_sink")

    def __init__(
        self,
        *,
        sink: EventSink,
        clock: EventClock,
        redactor: Redactor,
        minimum_level: LogLevel,
    ) -> None:
        """Store injected collaborators without configuring process-global state."""
        if type(minimum_level) is not LogLevel:
            raise InvalidLogEventError("A configured minimum LogLevel is required.")
        self._sink = sink
        self._clock = clock
        self._redactor = redactor
        self._minimum_level = minimum_level

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        """Emit one structured event with context and exception classification."""
        if type(level) is not LogLevel:
            raise InvalidLogEventError("A configured LogLevel is required.")
        if (
            type(event_name) is not str
            or not event_name
            or event_name.isspace()
            or len(event_name) > 128
        ):
            raise InvalidLogEventError("A non-blank event name is required.")
        if fields is not None and not isinstance(fields, Mapping):
            raise InvalidLogEventError("Structured log fields must be a mapping.")
        if error is not None and not isinstance(error, BaseException):
            raise InvalidLogEventError("A logging error must be an exception.")
        if _LEVEL_PRIORITY[level] < _LEVEL_PRIORITY[self._minimum_level]:
            return

        context = current_log_context()
        raw_event: dict[str, object] = {
            "timestamp": _timestamp_from_clock(self._clock),
            "level": level.value,
            "event_name": event_name,
            "correlation_id": None,
        }
        if context is not None:
            raw_event.update(context.as_fields())
        if fields is not None:
            for name, value in fields.items():
                if type(name) is not str or name in _RESERVED_FIELDS:
                    raise InvalidLogEventError(
                        "Structured log fields cannot replace reserved fields."
                    )
                raw_event[name] = value
        if error is not None:
            classification = classify_error(error)
            raw_event.update(
                {
                    "error_type": type(error).__name__,
                    "error_category": classification.category.value,
                    "error_message": str(error),
                }
            )

        redacted = self._redactor.redact(raw_event)
        if not isinstance(redacted, dict):
            raise InvalidLogEventError("Structured event redaction failed.")
        self._sink(redacted)


__all__ = (
    "EventClock",
    "EventSink",
    "InvalidLogEventError",
    "StructuredEvent",
    "StructuredLogger",
    "format_json_event",
)
