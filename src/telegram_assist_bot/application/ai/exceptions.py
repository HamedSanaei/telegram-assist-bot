"""AI parsing and validation exceptions."""

from __future__ import annotations

from telegram_assist_bot.shared.errors import ValidationError


class AIResponseError(ValidationError):
    """Base class for all AI response parsing and validation errors."""

    safe_message = "AI response validation failed."


class AIEmptyResponseError(AIResponseError):
    """Raised when the AI response payload is empty."""

    safe_message = "AI response is empty."


class AIInvalidJSONError(AIResponseError):
    """Raised when the AI response payload is not valid JSON."""

    safe_message = "AI response is not valid JSON."


class AISchemaValidationError(AIResponseError):
    """Raised when the AI response does not match the expected schema structure."""

    safe_message = "AI response does not match the schema."


class AIValidationConstraintError(AIResponseError):
    """Raised when the AI response violates specific schema constraints or limits."""

    safe_message = "AI response violates schema constraints."


class AIRepairFailedError(AIResponseError):
    """Raised when a response failed to be repaired."""

    safe_message = "AI response repair failed."
