from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.native_scheduling import RunNativeScheduling
from telegram_assist_bot.application.ports import (
    NativeScheduleCommand,
    NativeScheduledMessage,
    NativeScheduleReceipt,
    NativeScheduleStatus,
    PublicationPayload,
)

if TYPE_CHECKING:
    from datetime import datetime as datetime_type


NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)


class Repository:
    def __init__(self, command: NativeScheduleCommand) -> None:
        self.command = command
        self.receipt: NativeScheduleReceipt | None = None
        self.failed: tuple[str, bool] | None = None
        self.claim_available = True
        self.acquired = True
        self.started = True
        self.reconciliation: NativeScheduleCommand | None = None
        self.reconciled: tuple[NativeScheduleStatus, datetime | None] | None = None

    async def claim_next(self, **kwargs: object) -> NativeScheduleCommand | None:
        del kwargs
        if not self.claim_available:
            return None
        value, self.command = (
            self.command,
            replace(self.command, status=NativeScheduleStatus.RESOLVED),
        )
        return value

    async def acquire_destination(self, *args: object, **kwargs: object) -> bool:
        del args, kwargs
        return self.acquired

    async def release_destination(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    async def mark_request_started(
        self, command_id: str, *, owner: str, due_at: datetime_type | None = None
    ) -> bool:
        del command_id, owner, due_at
        return self.started

    async def complete_scheduled(
        self,
        command_id: str,
        *,
        owner: str,
        receipt: NativeScheduleReceipt,
        now: datetime_type,
    ) -> NativeScheduleCommand:
        del command_id, owner, now
        self.receipt = receipt
        return replace(
            self.command,
            status=NativeScheduleStatus.SCHEDULED,
            due_at=receipt.due_at,
            telegram_message_ids=receipt.message_ids,
        )

    async def complete_cancelled(
        self, command_id: str, *, owner: str
    ) -> NativeScheduleCommand:
        del command_id, owner
        return replace(self.command, status=NativeScheduleStatus.CANCELLED)

    async def fail(self, command_id: str, **kwargs: object) -> None:
        del command_id
        self.failed = (str(kwargs["failure_type"]), bool(kwargs["outcome_unknown"]))

    async def claim_reconciliation(
        self, **kwargs: object
    ) -> NativeScheduleCommand | None:
        del kwargs
        value, self.reconciliation = self.reconciliation, None
        return value

    async def complete_reconciliation(
        self,
        command_id: str,
        *,
        owner: str,
        status: NativeScheduleStatus,
        due_at: datetime,
        next_check_at: datetime | None,
    ) -> NativeScheduleCommand:
        del command_id, owner, next_check_at
        self.reconciled = (status, due_at)
        return replace(self.command, status=status, due_at=due_at)


class Loader:
    async def load(self, post_id: str, destination_id: int) -> PublicationPayload:
        del post_id
        return PublicationPayload(destination_id, "سلام", ())


class Gateway:
    def __init__(self) -> None:
        self.existing: tuple[NativeScheduledMessage, ...] = (
            NativeScheduledMessage(90, NOW + timedelta(minutes=20)),
        )
        self.due_at: datetime | None = None
        self.cancelled: tuple[int, ...] | None = None
        self.list_error = False
        self.schedule_error = False
        self.cancel_error = False

    async def list_scheduled(
        self, *args: object, **kwargs: object
    ) -> tuple[NativeScheduledMessage, ...]:
        del args, kwargs
        if self.list_error:
            raise TimeoutError
        return self.existing

    async def schedule(
        self, payload: PublicationPayload, *, due_at: datetime, timeout_seconds: float
    ) -> NativeScheduleReceipt:
        del payload, timeout_seconds
        if self.schedule_error:
            raise TimeoutError
        self.due_at = due_at
        return NativeScheduleReceipt((101,), due_at)

    async def cancel(
        self,
        destination_id: int,
        message_ids: tuple[int, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        del destination_id, timeout_seconds
        if self.cancel_error:
            raise TimeoutError
        self.cancelled = message_ids


def test_native_worker_uses_latest_telegram_slot_plus_exactly_five_minutes() -> None:
    async def scenario() -> None:
        command = NativeScheduleCommand(
            "command", "post", -1001, 3, NativeScheduleStatus.PENDING
        )
        repository = Repository(command)
        gateway = Gateway()
        worker = RunNativeScheduling(
            repository,  # type: ignore[arg-type]
            gateway,
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert await worker.execute_once()
        assert gateway.due_at == NOW + timedelta(minutes=25)
        assert repository.receipt == NativeScheduleReceipt(
            (101,), NOW + timedelta(minutes=25)
        )

    asyncio.run(scenario())


def test_native_worker_validates_lease_and_runs_completion_callbacks() -> None:
    command = NativeScheduleCommand(
        "command", "post", -1001, 3, NativeScheduleStatus.PENDING
    )
    with pytest.raises(ValueError, match="lease"):
        RunNativeScheduling(
            Repository(command),  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=30,
            retry_seconds=1,
        )

    async def scenario() -> None:
        scheduled_results: list[NativeScheduleCommand] = []

        async def after_scheduled(value: NativeScheduleCommand) -> None:
            scheduled_results.append(value)

        worker = RunNativeScheduling(
            Repository(command),  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
            after_scheduled=after_scheduled,
        )
        assert await worker.execute_once()
        assert scheduled_results[0].status is NativeScheduleStatus.SCHEDULED

        cancelled_results: list[NativeScheduleCommand] = []

        async def after_cancelled(value: NativeScheduleCommand) -> None:
            cancelled_results.append(value)

        cancellation = replace(
            command,
            status=NativeScheduleStatus.CANCEL_REQUESTED,
            operation="cancel",
        )
        worker = RunNativeScheduling(
            Repository(cancellation),  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
            after_cancelled=after_cancelled,
        )
        assert await worker.execute_once()
        assert cancelled_results[0].status is NativeScheduleStatus.CANCELLED

        lost_repository = Repository(command)
        lost_repository.started = False
        lost = RunNativeScheduling(
            lost_repository,  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert await lost.execute_once()
        assert lost_repository.failed == ("RuntimeError", False)

    asyncio.run(scenario())


def test_native_worker_covers_idle_busy_and_request_certainty_boundaries() -> None:
    async def scenario() -> None:
        command = NativeScheduleCommand(
            "command", "post", -1001, 3, NativeScheduleStatus.PENDING
        )

        idle_repository = Repository(command)
        idle_repository.claim_available = False
        idle = RunNativeScheduling(
            idle_repository,  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert not await idle.execute_once()

        busy_repository = Repository(command)
        busy_repository.acquired = False
        busy = RunNativeScheduling(
            busy_repository,  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert await busy.execute_once()
        assert busy_repository.failed == ("NativeDestinationBusy", False)

        before_repository = Repository(command)
        before_gateway = Gateway()
        before_gateway.list_error = True
        before = RunNativeScheduling(
            before_repository,  # type: ignore[arg-type]
            before_gateway,
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert await before.execute_once()
        assert before_repository.failed == ("TimeoutError", False)

        after_repository = Repository(command)
        after_gateway = Gateway()
        after_gateway.schedule_error = True
        after = RunNativeScheduling(
            after_repository,  # type: ignore[arg-type]
            after_gateway,
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert await after.execute_once()
        assert after_repository.failed == ("TimeoutError", True)

    asyncio.run(scenario())


def test_native_reconciliation_distinguishes_presence_and_safe_absence() -> None:
    async def reconcile(
        *,
        existing: tuple[NativeScheduledMessage, ...],
        due_at: datetime,
        list_error: bool = False,
    ) -> tuple[NativeScheduleStatus, datetime | None]:
        command = NativeScheduleCommand(
            "command",
            "post",
            -1001,
            3,
            NativeScheduleStatus.SCHEDULED,
            due_at=due_at,
            telegram_message_ids=(7, 8),
        )
        repository = Repository(command)
        repository.reconciliation = command
        gateway = Gateway()
        gateway.existing = existing
        gateway.list_error = list_error
        callbacks: list[NativeScheduleCommand] = []

        async def after_reconciled(value: NativeScheduleCommand) -> None:
            callbacks.append(value)

        worker = RunNativeScheduling(
            repository,  # type: ignore[arg-type]
            gateway,
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
            after_reconciled=after_reconciled,
        )
        assert await worker.reconcile_once()
        assert repository.reconciled is not None
        if not list_error:
            assert callbacks
        return repository.reconciled

    async def scenario() -> None:
        command = NativeScheduleCommand(
            "idle", "post", -1, 1, NativeScheduleStatus.SCHEDULED
        )
        idle_repository = Repository(command)
        idle = RunNativeScheduling(
            idle_repository,  # type: ignore[arg-type]
            Gateway(),
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert not await idle.reconcile_once()

        present = (
            NativeScheduledMessage(7, NOW + timedelta(minutes=5)),
            NativeScheduledMessage(8, NOW + timedelta(minutes=5)),
        )
        assert (await reconcile(existing=present, due_at=present[0].due_at))[0] is (
            NativeScheduleStatus.SCHEDULED
        )
        partial = (present[0],)
        assert (await reconcile(existing=partial, due_at=present[0].due_at))[0] is (
            NativeScheduleStatus.OUTCOME_UNKNOWN
        )
        assert (await reconcile(existing=(), due_at=NOW + timedelta(minutes=5)))[
            0
        ] is NativeScheduleStatus.CANCELLED_EXTERNAL
        assert (await reconcile(existing=(), due_at=NOW - timedelta(seconds=1)))[
            0
        ] is NativeScheduleStatus.RESOLVED
        assert (
            await reconcile(
                existing=present,
                due_at=present[0].due_at,
                list_error=True,
            )
        )[0] is NativeScheduleStatus.SCHEDULED

    asyncio.run(scenario())


def test_native_worker_deletes_persisted_ids_for_cancellation() -> None:
    async def scenario() -> None:
        command = NativeScheduleCommand(
            "command",
            "post",
            -1001,
            4,
            NativeScheduleStatus.CANCEL_REQUESTED,
            due_at=NOW + timedelta(minutes=5),
            telegram_message_ids=(7, 8),
            operation="cancel",
        )
        gateway = Gateway()
        worker = RunNativeScheduling(
            Repository(command),  # type: ignore[arg-type]
            gateway,
            Loader(),
            owner="runtime",
            clock=lambda: NOW,
            timeout_seconds=30,
            lease_seconds=60,
            retry_seconds=1,
        )
        assert await worker.execute_once()
        assert gateway.cancelled == (7, 8)

    asyncio.run(scenario())
