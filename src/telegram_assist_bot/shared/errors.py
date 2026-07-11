"""Application-owned failure taxonomy for provider-independent workflows."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar


class ErrorCategory(StrEnum):
    """Stable categories emitted by application errors and observability events."""

    VALIDATION = "validation"
    CONFIGURATION = "configuration"
    AUTHORIZATION = "authorization"
    PERMISSION = "permission"
    PERMANENT = "permanent"
    TRANSIENT = "transient"
    TIMEOUT = "timeout"
    RATE_LIMIT = "rate_limit"
    CONCURRENCY_CONFLICT = "concurrency_conflict"
    ALREADY_COMPLETED = "already_completed"


_RETRYABLE_CATEGORIES = frozenset(
    {
        ErrorCategory.TRANSIENT,
        ErrorCategory.TIMEOUT,
        ErrorCategory.RATE_LIMIT,
    }
)


@dataclass(frozen=True, slots=True)
class ErrorClassification:
    """Describe a stable error category and whether policy may retry it."""

    category: ErrorCategory
    retryable: bool

    def __post_init__(self) -> None:
        """Prevent construction of category and retryability contradictions."""
        if type(self.category) is not ErrorCategory or type(self.retryable) is not bool:
            raise TypeError("Error classification values must use owned types.")
        if self.retryable is not (self.category in _RETRYABLE_CATEGORIES):
            raise ValueError("Error classification retryability is inconsistent.")


class ApplicationError(Exception):
    """Base failure with a fixed safe message and optional preserved cause."""

    category: ClassVar[ErrorCategory] = ErrorCategory.PERMANENT
    safe_message: ClassVar[str] = "The application operation failed."

    def __init__(self, *, cause: BaseException | None = None) -> None:
        """Initialize the fixed message and retain a cause without echoing it."""
        super().__init__(self.safe_message)
        if cause is not None:
            self.__cause__ = cause

    @property
    def classification(self) -> ErrorClassification:
        """Return immutable, non-sensitive classification metadata."""
        return ErrorClassification(
            category=self.category,
            retryable=self.category in _RETRYABLE_CATEGORIES,
        )


class ValidationError(ApplicationError):
    """Report invalid application input that must not be retried."""

    category = ErrorCategory.VALIDATION
    safe_message = "Application input is invalid."


class ConfigurationError(ApplicationError):
    """Report invalid or unavailable configuration that must not be retried."""

    category = ErrorCategory.CONFIGURATION
    safe_message = "Application configuration is invalid."


class AuthorizationError(ApplicationError):
    """Report missing or invalid authorization that must not be retried."""

    category = ErrorCategory.AUTHORIZATION
    safe_message = "Authorization failed."


class PermissionDeniedError(ApplicationError):
    """Report insufficient permission that must not be retried."""

    category = ErrorCategory.PERMISSION
    safe_message = "Permission was denied."


class PermanentOperationError(ApplicationError):
    """Report a permanent operational failure that must not be retried."""

    category = ErrorCategory.PERMANENT
    safe_message = "The operation failed permanently."


class TransientOperationError(ApplicationError):
    """Report a temporary failure that retry policy may retry."""

    category = ErrorCategory.TRANSIENT
    safe_message = "The operation failed temporarily."


class OperationTimeoutError(ApplicationError):
    """Report an operation timeout that retry policy may retry."""

    category = ErrorCategory.TIMEOUT
    safe_message = "The operation timed out."


class RateLimitError(ApplicationError):
    """Report rate limiting that retry policy may retry."""

    category = ErrorCategory.RATE_LIMIT
    safe_message = "The operation was rate limited."


class ConcurrencyConflictError(ApplicationError):
    """Report a concurrency conflict that is not automatically retried."""

    category = ErrorCategory.CONCURRENCY_CONFLICT
    safe_message = "The operation conflicted with a concurrent change."


class AlreadyCompletedError(ApplicationError):
    """Report an idempotent operation that was already completed."""

    category = ErrorCategory.ALREADY_COMPLETED
    safe_message = "The operation was already completed."


def classify_error(error: BaseException) -> ErrorClassification:
    """Classify known application errors; unknown failures are permanent."""
    if isinstance(error, ApplicationError):
        return error.classification
    raw_category = getattr(type(error), "error_category", None)
    if type(raw_category) is str:
        try:
            category = ErrorCategory(raw_category)
        except ValueError:
            category = ErrorCategory.PERMANENT
        return ErrorClassification(
            category=category,
            retryable=category in _RETRYABLE_CATEGORIES,
        )
    if isinstance(error, TimeoutError):
        return ErrorClassification(category=ErrorCategory.TIMEOUT, retryable=True)
    return ErrorClassification(category=ErrorCategory.PERMANENT, retryable=False)


__all__ = (
    "AlreadyCompletedError",
    "ApplicationError",
    "AuthorizationError",
    "ConcurrencyConflictError",
    "ConfigurationError",
    "ErrorCategory",
    "ErrorClassification",
    "OperationTimeoutError",
    "PermanentOperationError",
    "PermissionDeniedError",
    "RateLimitError",
    "TransientOperationError",
    "ValidationError",
    "classify_error",
)
