from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    REDACTION_MARKER,
    CorrelationContext,
    InvalidCorrelationContextError,
    InvalidLogEventError,
    RedactedValue,
    Redactor,
    StructuredEvent,
    StructuredLogger,
    bind_log_context,
    current_log_context,
    format_json_event,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

_NOW = datetime(2026, 7, 11, 10, 20, 30, 456789, tzinfo=UTC)
_PRIVATE_SENTINEL = "private" + "-log-fixture"


def _run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


def _logger(events: list[dict[str, RedactedValue]]) -> StructuredLogger:
    def capture(event: StructuredEvent) -> None:
        events.append(dict(event))

    return StructuredLogger(
        sink=capture,
        clock=lambda: _NOW,
        redactor=Redactor(secret_values=(_PRIVATE_SENTINEL,)),
        minimum_level=LogLevel.DEBUG,
    )


def test_structured_event_has_base_fields_context_and_utf8_json() -> None:
    events: list[dict[str, RedactedValue]] = []
    logger = _logger(events)
    context = CorrelationContext(
        correlation_id="corr-t005-fa",
        task_id="task-observability",
        job_id="job-42",
        post_id="post-7",
        channel_id=-100123,
        destination_id=-100456,
        admin_id=123,
    )

    with bind_log_context(context):
        logger.emit(
            level=LogLevel.INFO,
            event_name="post.observed",
            fields={"status": "پردازش شد ✅"},
        )

    assert events == [
        {
            "timestamp": "2026-07-11T10:20:30.456789+00:00",
            "level": "INFO",
            "event_name": "post.observed",
            "correlation_id": "corr-t005-fa",
            "task_id": "task-observability",
            "job_id": "job-42",
            "post_id": "post-7",
            "channel_id": -100123,
            "destination_id": -100456,
            "admin_id": 123,
            "status": "پردازش شد ✅",
        }
    ]
    encoded = format_json_event(events[0], redactor=Redactor())
    assert "پردازش شد ✅" in encoded
    assert "\\u06" not in encoded.lower()
    assert json.loads(encoded) == events[0]


def test_exception_fields_are_classified_and_redacted() -> None:
    events: list[dict[str, RedactedValue]] = []
    logger = _logger(events)
    error = RuntimeError(f"خطا token={_PRIVATE_SENTINEL} در پردازش فارسی باقی ماند")

    logger.emit(
        level=LogLevel.ERROR,
        event_name="operation.failed",
        error=error,
    )

    event = events[0]
    assert event["error_type"] == "RuntimeError"
    assert event["error_category"] == "permanent"
    assert event["error_message"] == (
        f"خطا token={REDACTION_MARKER} در پردازش فارسی باقی ماند"
    )
    assert _PRIVATE_SENTINEL not in format_json_event(event, redactor=Redactor())


def test_full_telegram_content_is_not_logged_by_default() -> None:
    events: list[dict[str, RedactedValue]] = []
    logger = _logger(events)

    logger.emit(
        level=LogLevel.INFO,
        event_name="candidate.created",
        fields={
            "post_content": "متن کامل پست با ایموجی ویژه ✨",
            "caption": "کپشن محرمانه",
            "raw_text": "متن خام",
            "telegram_payload": "payload خام Telegram",
            "body": "بدنهٔ کامل پیام",
            "post_id_hint": "safe-reference",
        },
    )

    assert events[0]["post_content"] == REDACTION_MARKER
    assert events[0]["caption"] == REDACTION_MARKER
    assert events[0]["raw_text"] == REDACTION_MARKER
    assert events[0]["telegram_payload"] == REDACTION_MARKER
    assert events[0]["body"] == REDACTION_MARKER
    assert events[0]["post_id_hint"] == "safe-reference"


def test_configured_minimum_level_filters_lower_events() -> None:
    events: list[dict[str, RedactedValue]] = []

    def capture(event: StructuredEvent) -> None:
        events.append(dict(event))

    logger = StructuredLogger(
        sink=capture,
        clock=lambda: _NOW,
        redactor=Redactor(),
        minimum_level=LogLevel.WARNING,
    )

    logger.emit(level=LogLevel.INFO, event_name="filtered.info")
    logger.emit(level=LogLevel.WARNING, event_name="visible.warning")

    assert [event["event_name"] for event in events] == ["visible.warning"]


def test_context_survives_awaits_and_is_isolated_between_coroutines() -> None:
    async def scenario() -> list[dict[str, RedactedValue]]:
        events: list[dict[str, RedactedValue]] = []
        logger = _logger(events)
        first_bound = asyncio.Event()
        second_bound = asyncio.Event()
        release = asyncio.Event()

        async def worker(
            correlation_id: str,
            post_id: str,
            bound: asyncio.Event,
        ) -> None:
            with bind_log_context(
                CorrelationContext(
                    correlation_id=correlation_id,
                    post_id=post_id,
                )
            ):
                bound.set()
                await release.wait()
                logger.emit(
                    level=LogLevel.INFO,
                    event_name="worker.completed",
                )

        first = asyncio.create_task(worker("corr-first", "post-first", first_bound))
        second = asyncio.create_task(worker("corr-second", "post-second", second_bound))
        await first_bound.wait()
        await second_bound.wait()
        release.set()
        await asyncio.gather(first, second)
        assert current_log_context() is None
        return events

    events = _run(scenario())

    pairs = {(event["correlation_id"], event["post_id"]) for event in events}
    assert pairs == {
        ("corr-first", "post-first"),
        ("corr-second", "post-second"),
    }


def test_nested_context_is_restored_on_exception() -> None:
    outer = CorrelationContext(correlation_id="corr-outer")
    inner = CorrelationContext(correlation_id="corr-inner")

    def fail_in_inner_context() -> None:
        with bind_log_context(inner):
            assert current_log_context() is inner
            raise RuntimeError("synthetic failure")

    with bind_log_context(outer):
        with pytest.raises(RuntimeError, match="synthetic failure"):
            fail_in_inner_context()
        assert current_log_context() is outer

    assert current_log_context() is None


@pytest.mark.parametrize(
    "invalid_context",
    [
        {"correlation_id": ""},
        {"correlation_id": "   "},
        {"correlation_id": "corr", "post_id": ""},
        {"correlation_id": "corr", "channel_id": 0},
        {"correlation_id": "corr", "admin_id": True},
    ],
)
def test_context_rejects_invalid_identifiers(
    invalid_context: dict[str, object],
) -> None:
    with pytest.raises(InvalidCorrelationContextError):
        CorrelationContext(**invalid_context)  # type: ignore[arg-type]


def test_logger_rejects_reserved_fields_and_naive_clocks() -> None:
    logger = _logger([])
    with pytest.raises(InvalidLogEventError):
        logger.emit(
            level=LogLevel.INFO,
            event_name="invalid.override",
            fields={"correlation_id": "override"},
        )

    naive_logger = StructuredLogger(
        sink=lambda _event: None,
        clock=lambda: _NOW.replace(tzinfo=None),
        redactor=Redactor(),
        minimum_level=LogLevel.DEBUG,
    )
    with pytest.raises(InvalidLogEventError):
        naive_logger.emit(level=LogLevel.INFO, event_name="invalid.clock")
