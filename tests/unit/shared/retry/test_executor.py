"""Verify asynchronous retry execution without real sleeps or I/O."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.errors import (
    AuthorizationError,
    ConfigurationError,
    OperationTimeoutError,
    PermanentOperationError,
    PermissionDeniedError,
    RateLimitError,
    TransientOperationError,
    ValidationError,
)
from telegram_assist_bot.shared.observability import (
    Redactor,
    StructuredEvent,
    StructuredLogger,
)
from telegram_assist_bot.shared.retry import RetryPolicy, execute_with_retry

if TYPE_CHECKING:
    from collections.abc import Mapping


@dataclass(frozen=True, slots=True)
class CapturedEvent:
    level: LogLevel
    event_name: str
    fields: Mapping[str, object]
    error: BaseException | None


class CapturingLogger:
    def __init__(self) -> None:
        self.events: list[CapturedEvent] = []

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.events.append(CapturedEvent(level, event_name, fields or {}, error))


class EventCollector:
    def __init__(self) -> None:
        self.events: list[StructuredEvent] = []

    def __call__(self, event: StructuredEvent) -> None:
        self.events.append(event)


def _policy(max_attempts: int = 3) -> RetryPolicy:
    return RetryPolicy(
        max_attempts=max_attempts,
        initial_delay_seconds=1,
        max_delay_seconds=10,
        backoff_multiplier=2,
        jitter_ratio=0,
    )


async def _unexpected_sleeper(_delay: float) -> None:
    pytest.fail("this scenario must not sleep")


def test_success_returns_without_retry_event_or_sleep() -> None:
    logger = CapturingLogger()
    delays: list[float] = []

    async def operation() -> str:
        return "done"

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    result = asyncio.run(
        execute_with_retry(
            operation,
            operation_name="safe_read",
            operation_is_safe_to_retry=True,
            policy=_policy(),
            logger=logger,
            sleeper=sleeper,
            jitter_source=lambda: 0.5,
        )
    )

    assert result == "done"
    assert delays == []
    assert logger.events == []


@pytest.mark.parametrize(
    "failure_type",
    [TransientOperationError, OperationTimeoutError, RateLimitError, TimeoutError],
)
def test_retryable_failure_succeeds_after_one_deterministic_retry(
    failure_type: type[Exception],
) -> None:
    logger = CapturingLogger()
    delays: list[float] = []
    attempts = 0

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise failure_type()
        return "recovered"

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    result = asyncio.run(
        execute_with_retry(
            operation,
            operation_name="idempotent_fetch",
            operation_is_safe_to_retry=True,
            policy=_policy(),
            logger=logger,
            sleeper=sleeper,
            jitter_source=lambda: 0.5,
        )
    )

    assert result == "recovered"
    assert attempts == 2
    assert delays == [1.0]
    assert len(logger.events) == 1
    event = logger.events[0]
    assert event.level is LogLevel.WARNING
    assert event.event_name == "retry_scheduled"
    assert event.fields == {
        "operation_name": "idempotent_fetch",
        "attempt": 1,
        "max_attempts": 3,
        "next_attempt": 2,
        "delay_seconds": 1.0,
    }
    assert isinstance(event.error, failure_type)


def test_exhaustion_uses_bounded_attempts_and_preserves_final_exception() -> None:
    logger = CapturingLogger()
    delays: list[float] = []
    failures = [
        TransientOperationError(),
        TransientOperationError(),
        TransientOperationError(),
    ]
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        failure = failures[attempts]
        attempts += 1
        raise failure

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    with pytest.raises(TransientOperationError) as caught:
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="idempotent_write",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert caught.value is failures[-1]
    assert attempts == 3
    assert delays == [1.0, 2.0]
    assert [event.event_name for event in logger.events] == [
        "retry_scheduled",
        "retry_scheduled",
        "retry_exhausted",
    ]
    final_event = logger.events[-1]
    assert final_event.level is LogLevel.ERROR
    assert final_event.fields == {
        "operation_name": "idempotent_write",
        "attempt": 3,
        "max_attempts": 3,
        "reason": "max_attempts_exhausted",
    }
    assert final_event.error is failures[-1]


@pytest.mark.parametrize(
    "failure",
    [
        ValidationError(),
        ConfigurationError(),
        AuthorizationError(),
        PermissionDeniedError(),
        PermanentOperationError(),
        RuntimeError("unknown"),
    ],
)
def test_non_retryable_failure_ends_immediately(failure: Exception) -> None:
    logger = CapturingLogger()
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    async def sleeper(_delay: float) -> None:
        pytest.fail("non-retryable failure must not sleep")

    with pytest.raises(type(failure)) as caught:
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="unsafe_failure",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert caught.value is failure
    assert attempts == 1
    assert len(logger.events) == 1
    assert logger.events[0].event_name == "retry_exhausted"
    assert logger.events[0].fields["reason"] == "non_retryable"


def test_final_failure_event_is_redacted_by_structured_logger() -> None:
    private_value = "private-retry-sentinel"
    collector = EventCollector()
    logger = StructuredLogger(
        sink=collector,
        clock=lambda: datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        redactor=Redactor(secret_values=(private_value,)),
        minimum_level=LogLevel.DEBUG,
    )

    async def operation() -> None:
        raise RuntimeError(f"خطای فارسی Authorization=Bearer {private_value}")

    with pytest.raises(RuntimeError):
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="redacted_failure",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert len(collector.events) == 1
    assert private_value not in repr(collector.events)
    assert "خطای فارسی" in str(collector.events[0]["error_message"])


def test_retry_attempt_event_is_redacted_by_structured_logger() -> None:
    private_value = "private" + "-retry-attempt-value"
    collector = EventCollector()
    logger = StructuredLogger(
        sink=collector,
        clock=lambda: datetime(2026, 7, 11, 12, 0, tzinfo=UTC),
        redactor=Redactor(secret_values=(private_value,)),
        minimum_level=LogLevel.DEBUG,
    )
    attempts = 0
    delays: list[float] = []

    async def operation() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise TimeoutError(f"خطای فارسی Authorization: Bearer {private_value}")
        return "recovered"

    async def sleeper(delay: float) -> None:
        delays.append(delay)

    result = asyncio.run(
        execute_with_retry(
            operation,
            operation_name="redacted_retry_attempt",
            operation_is_safe_to_retry=True,
            policy=_policy(),
            logger=logger,
            sleeper=sleeper,
            jitter_source=lambda: 0.5,
        )
    )

    assert result == "recovered"
    assert delays == [1.0]
    assert len(collector.events) == 1
    event = collector.events[0]
    assert event["event_name"] == "retry_scheduled"
    assert private_value not in repr(event)
    assert "خطای فارسی" in str(event["error_message"])


def test_single_attempt_policy_performs_zero_retries() -> None:
    logger = CapturingLogger()
    failure = TransientOperationError()

    async def operation() -> None:
        raise failure

    with pytest.raises(TransientOperationError) as caught:
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="one_shot",
                operation_is_safe_to_retry=True,
                policy=_policy(max_attempts=1),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert caught.value is failure
    assert len(logger.events) == 1
    assert logger.events[0].fields["attempt"] == 1


def test_cancellation_propagates_without_retry_or_logging() -> None:
    logger = CapturingLogger()

    async def operation() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="cancelled",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert logger.events == []


def test_cancellation_during_backoff_propagates_immediately() -> None:
    logger = CapturingLogger()
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise TransientOperationError

    async def cancelled_sleeper(_delay: float) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="cancelled_backoff",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=cancelled_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert attempts == 1
    assert [event.event_name for event in logger.events] == ["retry_scheduled"]


def test_wrapped_cancellation_is_unwrapped_without_retry() -> None:
    logger = CapturingLogger()
    cancellation = asyncio.CancelledError()
    wrapped = TransientOperationError(cause=cancellation)
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise wrapped

    with pytest.raises(asyncio.CancelledError) as captured:
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="wrapped_cancellation",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert captured.value is cancellation
    assert attempts == 1
    assert logger.events == []


def test_deep_cancellation_cause_chain_is_never_retried() -> None:
    logger = CapturingLogger()
    cancellation = asyncio.CancelledError()
    wrapped: BaseException = cancellation
    for _index in range(32):
        wrapped = TransientOperationError(cause=wrapped)
    assert isinstance(wrapped, TransientOperationError)
    attempts = 0

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise wrapped

    with pytest.raises(asyncio.CancelledError) as captured:
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="deep_wrapped_cancellation",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert captured.value is cancellation
    assert attempts == 1
    assert logger.events == []


def test_logger_failure_does_not_mask_the_operation_failure() -> None:
    failure = TransientOperationError()
    attempts = 0

    class FailingLogger:
        def emit(
            self,
            *,
            level: LogLevel,
            event_name: str,
            fields: Mapping[str, object] | None = None,
            error: BaseException | None = None,
        ) -> None:
            del level, event_name, fields, error
            raise RuntimeError("private" + "-logging-detail")

    async def operation() -> None:
        nonlocal attempts
        attempts += 1
        raise failure

    with pytest.raises(TransientOperationError) as captured:
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="logging_failure",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=FailingLogger(),
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert captured.value is failure
    assert attempts == 1
    assert captured.value.__notes__ == ["Structured retry event emission failed."]
    assert "private-logging-detail" not in repr(captured.value)


def test_operation_must_be_explicitly_safe_before_first_attempt() -> None:
    logger = CapturingLogger()
    called = False

    async def operation() -> None:
        nonlocal called
        called = True

    with pytest.raises(ValueError, match="explicitly safe"):
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="side_effect",
                operation_is_safe_to_retry=False,
                policy=_policy(),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert called is False
    assert logger.events == []


def test_operation_name_must_not_be_blank_before_first_attempt() -> None:
    logger = CapturingLogger()
    called = False

    async def operation() -> None:
        nonlocal called
        called = True

    with pytest.raises(ValueError, match="must not be blank"):
        asyncio.run(
            execute_with_retry(
                operation,
                operation_name="  ",
                operation_is_safe_to_retry=True,
                policy=_policy(),
                logger=logger,
                sleeper=_unexpected_sleeper,
                jitter_source=lambda: 0.5,
            )
        )

    assert called is False
