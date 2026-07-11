"""Task-local structured logging context for asynchronous workflows."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

type ContextIdentifier = str | int


class InvalidCorrelationContextError(ValueError):
    """Report an invalid, non-sensitive logging context value."""


def _validate_identifier(value: ContextIdentifier | None) -> None:
    """Require optional identifiers to be exact, compact scalar values."""
    if value is None:
        return
    if type(value) is int:
        if value == 0:
            raise InvalidCorrelationContextError("Context identifiers cannot be zero.")
        return
    if type(value) is str and value and not value.isspace() and len(value) <= 256:
        return
    raise InvalidCorrelationContextError("Context identifiers must be non-blank.")


@dataclass(frozen=True, slots=True)
class CorrelationContext:
    """Carry allowlisted identifiers within one asynchronous task context."""

    correlation_id: str
    task_id: ContextIdentifier | None = None
    job_id: ContextIdentifier | None = None
    post_id: ContextIdentifier | None = None
    channel_id: ContextIdentifier | None = None
    destination_id: ContextIdentifier | None = None
    admin_id: ContextIdentifier | None = None

    def __post_init__(self) -> None:
        """Reject blank or oversized correlation and contextual identifiers."""
        if (
            type(self.correlation_id) is not str
            or not self.correlation_id
            or self.correlation_id.isspace()
            or len(self.correlation_id) > 256
        ):
            raise InvalidCorrelationContextError(
                "A non-blank correlation identifier is required."
            )
        for value in (
            self.task_id,
            self.job_id,
            self.post_id,
            self.channel_id,
            self.destination_id,
            self.admin_id,
        ):
            _validate_identifier(value)

    def as_fields(self) -> dict[str, ContextIdentifier]:
        """Return a fresh mapping containing only populated allowlisted fields."""
        fields: dict[str, ContextIdentifier] = {"correlation_id": self.correlation_id}
        optional_fields = (
            ("task_id", self.task_id),
            ("job_id", self.job_id),
            ("post_id", self.post_id),
            ("channel_id", self.channel_id),
            ("destination_id", self.destination_id),
            ("admin_id", self.admin_id),
        )
        fields.update(
            (name, value) for name, value in optional_fields if value is not None
        )
        return fields


_CURRENT_CONTEXT: ContextVar[CorrelationContext | None] = ContextVar(
    "telegram_assist_bot_log_context",
    default=None,
)


def current_log_context() -> CorrelationContext | None:
    """Return the immutable context bound to the current execution task."""
    return _CURRENT_CONTEXT.get()


@contextmanager
def bind_log_context(context: CorrelationContext) -> Iterator[CorrelationContext]:
    """Bind one context and restore the previous value on every exit path."""
    if type(context) is not CorrelationContext:
        raise InvalidCorrelationContextError("A CorrelationContext is required.")
    token = _CURRENT_CONTEXT.set(context)
    try:
        yield context
    finally:
        _CURRENT_CONTEXT.reset(token)


__all__ = (
    "ContextIdentifier",
    "CorrelationContext",
    "InvalidCorrelationContextError",
    "bind_log_context",
    "current_log_context",
)
