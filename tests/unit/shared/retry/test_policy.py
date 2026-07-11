"""Verify stable error classification and bounded retry policy calculations."""

from __future__ import annotations

import math
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from telegram_assist_bot.application.ports import (
    InvalidPostRepositoryRequestError,
    PostConcurrencyConflictError,
    PostRepositoryUnavailableError,
)
from telegram_assist_bot.shared.config import ConfigurationError as ExistingConfigError
from telegram_assist_bot.shared.errors import (
    AlreadyCompletedError,
    ApplicationError,
    AuthorizationError,
    ConcurrencyConflictError,
    ConfigurationError,
    ErrorCategory,
    ErrorClassification,
    OperationTimeoutError,
    PermanentOperationError,
    PermissionDeniedError,
    RateLimitError,
    TransientOperationError,
    ValidationError,
    classify_error,
)
from telegram_assist_bot.shared.retry import ExternalOperationPolicy, RetryPolicy


@pytest.mark.parametrize(
    ("failure_type", "category", "retryable"),
    [
        (ValidationError, ErrorCategory.VALIDATION, False),
        (ConfigurationError, ErrorCategory.CONFIGURATION, False),
        (AuthorizationError, ErrorCategory.AUTHORIZATION, False),
        (PermissionDeniedError, ErrorCategory.PERMISSION, False),
        (PermanentOperationError, ErrorCategory.PERMANENT, False),
        (TransientOperationError, ErrorCategory.TRANSIENT, True),
        (OperationTimeoutError, ErrorCategory.TIMEOUT, True),
        (RateLimitError, ErrorCategory.RATE_LIMIT, True),
        (ConcurrencyConflictError, ErrorCategory.CONCURRENCY_CONFLICT, False),
        (AlreadyCompletedError, ErrorCategory.ALREADY_COMPLETED, False),
    ],
)
def test_application_failures_have_stable_classification(
    failure_type: type[ApplicationError],
    category: ErrorCategory,
    retryable: bool,
) -> None:
    failure = failure_type()

    assert classify_error(failure).category is category
    assert classify_error(failure).retryable is retryable
    assert failure.classification == classify_error(failure)


def test_error_category_values_are_stable() -> None:
    assert tuple(category.value for category in ErrorCategory) == (
        "validation",
        "configuration",
        "authorization",
        "permission",
        "permanent",
        "transient",
        "timeout",
        "rate_limit",
        "concurrency_conflict",
        "already_completed",
    )


def test_application_error_preserves_cause_without_echoing_it() -> None:
    cause = RuntimeError("Bearer private-sentinel")
    failure = TransientOperationError(cause=cause)

    assert failure.__cause__ is cause
    assert str(failure) == "The operation failed temporarily."
    assert "private-sentinel" not in str(failure)


def test_unknown_failure_defaults_to_non_retryable_permanent() -> None:
    classification = classify_error(RuntimeError("unknown"))

    assert classification.category is ErrorCategory.PERMANENT
    assert classification.retryable is False


def test_builtin_timeout_is_retryable_without_provider_specific_types() -> None:
    classification = classify_error(TimeoutError("bounded operation timed out"))

    assert classification.category is ErrorCategory.TIMEOUT
    assert classification.retryable is True


@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        (
            ExistingConfigError("safe configuration failure"),
            ErrorCategory.CONFIGURATION,
            False,
        ),
        (InvalidPostRepositoryRequestError(), ErrorCategory.VALIDATION, False),
        (PostRepositoryUnavailableError(), ErrorCategory.TRANSIENT, True),
        (
            PostConcurrencyConflictError(),
            ErrorCategory.CONCURRENCY_CONFLICT,
            False,
        ),
    ],
)
def test_existing_application_errors_join_the_shared_taxonomy(
    error: Exception,
    category: ErrorCategory,
    retryable: bool,
) -> None:
    classification = classify_error(error)

    assert classification.category is category
    assert classification.retryable is retryable


def test_error_classification_rejects_inconsistent_retryability() -> None:
    with pytest.raises(ValueError, match="inconsistent"):
        ErrorClassification(ErrorCategory.VALIDATION, retryable=True)


def test_error_classification_is_immutable() -> None:
    classification = classify_error(TransientOperationError())

    with pytest.raises(FrozenInstanceError):
        classification.retryable = False  # type: ignore[misc]


@pytest.mark.parametrize("max_attempts", [0, 11, -1])
def test_retry_policy_rejects_attempts_outside_hard_bound(max_attempts: int) -> None:
    with pytest.raises(ValueError, match="between 1 and 10"):
        RetryPolicy(
            max_attempts=max_attempts,
            initial_delay_seconds=1,
            max_delay_seconds=10,
        )


def test_retry_policy_rejects_boolean_or_non_integer_attempts() -> None:
    with pytest.raises(TypeError, match="must be an integer"):
        RetryPolicy(
            max_attempts=cast("int", True),
            initial_delay_seconds=1,
            max_delay_seconds=10,
        )


@pytest.mark.parametrize(
    ("field", "value", "expected_error"),
    [
        ("initial_delay_seconds", -1.0, ValueError),
        ("max_delay_seconds", -1.0, ValueError),
        ("backoff_multiplier", 0.9, ValueError),
        ("jitter_ratio", -0.1, ValueError),
        ("jitter_ratio", 1.1, ValueError),
        ("initial_delay_seconds", math.inf, ValueError),
        ("max_delay_seconds", math.nan, ValueError),
        ("jitter_ratio", cast("float", True), TypeError),
    ],
)
def test_retry_policy_rejects_invalid_numeric_configuration(
    field: str,
    value: float,
    expected_error: type[Exception],
) -> None:
    values: dict[str, object] = {
        "max_attempts": 4,
        "initial_delay_seconds": 1.0,
        "max_delay_seconds": 10.0,
        "backoff_multiplier": 2.0,
        "jitter_ratio": 0.0,
    }
    values[field] = value

    with pytest.raises(expected_error):
        RetryPolicy(**values)  # type: ignore[arg-type]


def test_retry_policy_calculates_exponential_backoff_and_cap() -> None:
    policy = RetryPolicy(
        max_attempts=5,
        initial_delay_seconds=1,
        max_delay_seconds=5,
        backoff_multiplier=2,
    )

    assert [
        policy.delay_for_retry(retry_number, random_value=0.5)
        for retry_number in range(1, 5)
    ] == [1.0, 2.0, 4.0, 5.0]


def test_retry_policy_applies_deterministic_jitter_without_exceeding_cap() -> None:
    policy = RetryPolicy(
        max_attempts=4,
        initial_delay_seconds=4,
        max_delay_seconds=5,
        jitter_ratio=0.25,
    )

    assert policy.delay_for_retry(1, random_value=0.0) == 3.0
    assert policy.delay_for_retry(1, random_value=0.5) == 4.0
    assert policy.delay_for_retry(1, random_value=1.0) == 5.0
    assert policy.delay_for_retry(2, random_value=1.0) == 5.0


@pytest.mark.parametrize(
    ("retry_number", "random_value"),
    [(0, 0.5), (4, 0.5), (1, -0.1), (1, 1.1)],
)
def test_retry_policy_rejects_invalid_delay_requests(
    retry_number: int, random_value: float
) -> None:
    policy = RetryPolicy(
        max_attempts=4,
        initial_delay_seconds=1,
        max_delay_seconds=10,
    )

    with pytest.raises(ValueError, match=r"retry_number|random_value"):
        policy.delay_for_retry(retry_number, random_value=random_value)


def test_external_operation_policy_requires_explicit_positive_finite_timeout() -> None:
    assert ExternalOperationPolicy(timeout_seconds=2).timeout_seconds == 2.0

    for invalid in (0, -1, math.inf, math.nan):
        with pytest.raises(ValueError, match="timeout_seconds"):
            ExternalOperationPolicy(timeout_seconds=invalid)
    with pytest.raises(TypeError):
        ExternalOperationPolicy(timeout_seconds=cast("float", True))
