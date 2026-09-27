"""Unit proofs for the bounded approval outbox reconciliation loop."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import pytest

from telegram_assist_bot.application.operational_approval import (
    ApprovalOutboxReconciliationLoop,
)
from telegram_assist_bot.application.ports import ApprovalOutboxReconciliation

if TYPE_CHECKING:
    from telegram_assist_bot.shared.config import LogLevel

_NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class BatchRepository:
    """Return preset batches and record every requested batch size."""

    def __init__(self, batches: list[ApprovalOutboxReconciliation]) -> None:
        self.batches = list(batches)
        self.requested_limits: list[int] = []
        self.requested_guards: list[float] = []

    async def reconcile_missing_deliveries(
        self, *, limit: int, at: datetime, guard_seconds: float = 0.0
    ) -> ApprovalOutboxReconciliation:
        del at
        self.requested_limits.append(limit)
        self.requested_guards.append(guard_seconds)
        if not self.batches:
            return ApprovalOutboxReconciliation(0, 0, 0, True)
        return self.batches.pop(0)


class RecordingLogger:
    """Record structured events without touching a sink."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, object]]] = []

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: dict[str, object] | None = None,
        **kwargs: object,
    ) -> None:
        del level, kwargs
        self.events.append((event_name, dict(fields or {})))

    @property
    def event_names(self) -> list[str]:
        return [name for name, _fields in self.events]


def _loop(
    repository: BatchRepository,
    logger: RecordingLogger,
    *,
    pause_seconds: float = 0,
) -> tuple[ApprovalOutboxReconciliationLoop, list[float]]:
    sleeps: list[float] = []

    async def sleeper(seconds: float) -> None:
        sleeps.append(seconds)

    return (
        ApprovalOutboxReconciliationLoop(
            cast("Any", repository),
            batch_size=100,
            interval_seconds=300,
            pause_seconds=pause_seconds,
            clock=lambda: _NOW,
            guard_seconds=300,
            logger=cast("Any", logger),
            sleeper=sleeper,
        ),
        sleeps,
    )


def test_pass_runs_bounded_batches_and_reports_aggregates() -> None:
    async def scenario() -> None:
        repository = BatchRepository(
            [
                ApprovalOutboxReconciliation(100, 40, 60, False, _NOW),
                ApprovalOutboxReconciliation(100, 0, 100, False, _NOW),
                ApprovalOutboxReconciliation(50, 10, 40, True, _NOW),
            ]
        )
        logger = RecordingLogger()
        loop, sleeps = _loop(repository, logger)
        created = await loop.reconcile_once()
        assert created is True
        assert repository.requested_limits == [100, 100, 100]
        assert repository.requested_guards == [300, 300, 300]
        assert sleeps == [0, 0]
        assert logger.event_names == [
            "approval_outbox_reconciliation_started",
            "approval_outbox_reconciliation_batch_processed",
            "approval_outbox_reconciliation_batch_processed",
            "approval_outbox_reconciliation_completed",
        ]
        batch_fields = logger.events[1][1]
        assert batch_fields["scanned_count"] == 100
        assert batch_fields["created_count"] == 40
        assert batch_fields["existing_count"] == 60
        assert batch_fields["completed"] is False
        assert batch_fields["watermark"] == _NOW.astimezone(UTC).isoformat()
        completed_fields = logger.events[-1][1]
        assert completed_fields["scanned_count"] == 250
        assert completed_fields["created_count"] == 50
        assert completed_fields["existing_count"] == 200
        assert completed_fields["batch_count"] == 3

    asyncio.run(scenario())


def test_guard_window_reverification_stays_silent() -> None:
    """A pass that only re-verifies the trailing window must not log at all."""

    async def scenario() -> None:
        repository = BatchRepository(
            [ApprovalOutboxReconciliation(150, 0, 150, True, _NOW)]
        )
        logger = RecordingLogger()
        loop, _sleeps = _loop(repository, logger)
        created = await loop.reconcile_once()
        assert created is False
        assert repository.requested_guards == [300]
        assert logger.events == []

    asyncio.run(scenario())


def test_idle_pass_is_silent_and_reports_no_created_work() -> None:
    async def scenario() -> None:
        repository = BatchRepository([ApprovalOutboxReconciliation(0, 0, 0, True)])
        logger = RecordingLogger()
        loop, sleeps = _loop(repository, logger)
        created = await loop.reconcile_once()
        assert created is False
        assert repository.requested_limits == [100]
        assert sleeps == []
        assert logger.events == []

    asyncio.run(scenario())


def test_catch_up_pauses_between_batches() -> None:
    async def scenario() -> None:
        repository = BatchRepository(
            [
                ApprovalOutboxReconciliation(100, 100, 0, False, _NOW),
                ApprovalOutboxReconciliation(0, 0, 0, True),
            ]
        )
        logger = RecordingLogger()
        loop, sleeps = _loop(repository, logger, pause_seconds=2)
        await loop.reconcile_once()
        assert sleeps == [2]

    asyncio.run(scenario())


def test_run_reconciles_once_per_interval_until_cancelled() -> None:
    async def scenario() -> None:
        repository = BatchRepository([])
        logger = RecordingLogger()
        sleeps: list[float] = []

        async def sleeper(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) >= 2:
                raise asyncio.CancelledError

        loop = ApprovalOutboxReconciliationLoop(
            cast("Any", repository),
            batch_size=10,
            interval_seconds=300,
            pause_seconds=1,
            clock=lambda: _NOW,
            logger=cast("Any", logger),
            sleeper=sleeper,
        )
        with pytest.raises(asyncio.CancelledError):
            await loop.run()
        assert sleeps == [300, 300]
        assert repository.requested_limits == [10, 10]

    asyncio.run(scenario())


def test_rejects_unbounded_configuration() -> None:
    repository = BatchRepository([])
    with pytest.raises(ValueError, match="batch size"):
        ApprovalOutboxReconciliationLoop(
            cast("Any", repository),
            batch_size=0,
            interval_seconds=300,
            pause_seconds=1,
            clock=lambda: _NOW,
        )
    with pytest.raises(ValueError, match="interval"):
        ApprovalOutboxReconciliationLoop(
            cast("Any", repository),
            batch_size=10,
            interval_seconds=0,
            pause_seconds=1,
            clock=lambda: _NOW,
        )
    with pytest.raises(ValueError, match="pause"):
        ApprovalOutboxReconciliationLoop(
            cast("Any", repository),
            batch_size=10,
            interval_seconds=300,
            pause_seconds=-1,
            clock=lambda: _NOW,
        )
    with pytest.raises(ValueError, match="guard"):
        ApprovalOutboxReconciliationLoop(
            cast("Any", repository),
            batch_size=10,
            interval_seconds=300,
            pause_seconds=1,
            clock=lambda: _NOW,
            guard_seconds=-1,
        )


def test_watermark_field_is_serializable_when_absent() -> None:
    async def scenario() -> None:
        repository = BatchRepository(
            [ApprovalOutboxReconciliation(5, 1, 4, True, None)]
        )
        logger = RecordingLogger()
        loop, _sleeps = _loop(repository, logger)
        await loop.reconcile_once()
        assert logger.events[1][1]["watermark"] is None

    asyncio.run(scenario())


def test_loop_emits_no_secret_bearing_fields() -> None:
    async def scenario() -> None:
        repository = BatchRepository(
            [ApprovalOutboxReconciliation(3, 1, 2, True, _NOW + timedelta(seconds=1))]
        )
        logger = RecordingLogger()
        loop, _sleeps = _loop(repository, logger)
        await loop.reconcile_once()
        allowed = {
            "scanned_count",
            "created_count",
            "existing_count",
            "completed",
            "watermark",
            "batch_count",
            "duration_seconds",
        }
        for _name, fields in logger.events:
            assert set(fields) <= allowed

    asyncio.run(scenario())
