"""Provider-independent structured logging, context, and redaction API."""

from telegram_assist_bot.shared.observability.context import (
    ContextIdentifier,
    CorrelationContext,
    InvalidCorrelationContextError,
    bind_log_context,
    current_log_context,
)
from telegram_assist_bot.shared.observability.logging import (
    EventClock,
    EventSink,
    InvalidLogEventError,
    StructuredEvent,
    StructuredLogger,
    format_json_event,
)
from telegram_assist_bot.shared.observability.redaction import (
    REDACTION_MARKER,
    RedactedValue,
    Redactor,
)

__all__ = (
    "REDACTION_MARKER",
    "ContextIdentifier",
    "CorrelationContext",
    "EventClock",
    "EventSink",
    "InvalidCorrelationContextError",
    "InvalidLogEventError",
    "RedactedValue",
    "Redactor",
    "StructuredEvent",
    "StructuredLogger",
    "bind_log_context",
    "current_log_context",
    "format_json_event",
)
