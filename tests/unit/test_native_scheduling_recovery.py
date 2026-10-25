"""Exercise native-only transport recovery with real SDK exception identities."""

from __future__ import annotations

import asyncio
import errno
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest
from pymongo.errors import (
    AutoReconnect,
    ConfigurationError,
    ConnectionFailure,
    DuplicateKeyError,
    ExecutionTimeout,
    NetworkTimeout,
    NotPrimaryError,
    OperationFailure,
    ServerSelectionTimeoutError,
    WaitQueueTimeoutError,
)
from telethon import errors  # type: ignore[import-untyped]

from telegram_assist_bot.application.native_scheduling import RunNativeScheduling
from telegram_assist_bot.application.ports import (
    NativeScheduleCommand,
    NativeScheduleReceipt,
    NativeScheduleRepository,
    NativeScheduleStatus,
    PublicationPayload,
    PublicationPayloadLoader,
    TelegramNativeSchedulerGateway,
)
from telegram_assist_bot.application.ports.telegram_source_gateway import (
    TelegramRateLimitError,
)
from telegram_assist_bot.bootstrap.native_scheduling import (
    classify_native_scheduling_failure,
    run_native_scheduling_cycle,
)
from telegram_assist_bot.shared.errors import ErrorCategory
from telegram_assist_bot.shared.retry import RetryPolicy

NOW = datetime(2026, 10, 25, 4, 26, 42, tzinfo=UTC)
COMMAND = NativeScheduleCommand(
    "command", "post", -1001, 3, NativeScheduleStatus.CLAIMED
)
POLICY = RetryPolicy(
    max_attempts=10,
    initial_delay_seconds=1,
    max_delay_seconds=8,
    jitter_ratio=0.2,
)


def _repository() -> AsyncMock:
    repository = AsyncMock(spec=NativeScheduleRepository)
    repository.claim_next.return_value = None
    repository.claim_reconciliation.return_value = None
    repository.acquire_destination.return_value = True
    repository.mark_request_started.return_value = True
    return repository


def _gateway() -> AsyncMock:
    gateway = AsyncMock(spec=TelegramNativeSchedulerGateway)
    gateway.list_scheduled.return_value = ()
    gateway.schedule.return_value = NativeScheduleReceipt(
        (81,), NOW + timedelta(minutes=5)
    )
    return gateway


def _native(repository: AsyncMock, gateway: AsyncMock) -> RunNativeScheduling:
    loader = AsyncMock(spec=PublicationPayloadLoader)
    loader.load.return_value = PublicationPayload(-1001, "text", ())
    return RunNativeScheduling(
        repository,
        gateway,
        loader,
        owner="runtime",
        clock=lambda: NOW,
        timeout_seconds=30,
        lease_seconds=60,
        retry_seconds=1,
        classify_failure=classify_native_scheduling_failure,
    )


@pytest.mark.parametrize(
    ("error", "category", "retryable"),
    [
        (NetworkTimeout("private connection"), ErrorCategory.TIMEOUT, True),
        (AutoReconnect("private connection"), ErrorCategory.TRANSIENT, True),
        (ConnectionFailure("private connection"), ErrorCategory.TRANSIENT, True),
        (NotPrimaryError("stepdown"), ErrorCategory.TRANSIENT, True),
        (ServerSelectionTimeoutError("unavailable"), ErrorCategory.TIMEOUT, True),
        (WaitQueueTimeoutError("pool busy"), ErrorCategory.TIMEOUT, True),
        (ConnectionResetError("connection lost"), ErrorCategory.TRANSIENT, True),
        (
            ConnectionError("Cannot send requests while disconnected"),
            ErrorCategory.TRANSIENT,
            True,
        ),
        (OSError(errno.ENETUNREACH, "network lost"), ErrorCategory.TRANSIENT, True),
        (TimeoutError("operation timeout"), ErrorCategory.TIMEOUT, True),
        (errors.ServerError(None, "temporary"), ErrorCategory.TRANSIENT, True),
        (errors.TimedOutError(None, "timeout"), ErrorCategory.TIMEOUT, True),
        (errors.TimeoutError(None), ErrorCategory.TIMEOUT, True),
        (TelegramRateLimitError(90), ErrorCategory.RATE_LIMIT, True),
        (ConfigurationError("invalid configuration"), ErrorCategory.PERMANENT, False),
        (
            OperationFailure("authentication failed", code=18),
            ErrorCategory.PERMANENT,
            False,
        ),
        (OperationFailure("unauthorized", code=13), ErrorCategory.PERMANENT, False),
        (OperationFailure("invalid query", code=2), ErrorCategory.PERMANENT, False),
        (DuplicateKeyError("duplicate"), ErrorCategory.PERMANENT, False),
        (ExecutionTimeout("query budget exceeded"), ErrorCategory.PERMANENT, False),
        (errors.AuthKeyUnregisteredError(None), ErrorCategory.PERMANENT, False),
        (errors.ChannelPrivateError(None), ErrorCategory.PERMANENT, False),
        (errors.BadRequestError(None, "invalid"), ErrorCategory.PERMANENT, False),
        (PermissionError("private path"), ErrorCategory.PERMANENT, False),
        (OSError(errno.EINVAL, "invalid operation"), ErrorCategory.PERMANENT, False),
        (ValueError("programming error"), ErrorCategory.PERMANENT, False),
        (TypeError("programming error"), ErrorCategory.PERMANENT, False),
    ],
)
def test_only_known_transport_failures_are_recoverable(
    error: BaseException, category: ErrorCategory, retryable: bool
) -> None:
    classification = classify_native_scheduling_failure(error)
    assert classification.category is category
    assert classification.retryable is retryable


@pytest.mark.parametrize(
    "method", ["claim_next", "acquire_destination", "claim_reconciliation"]
)
def test_observed_database_network_timeout_before_operation_try_recovers(
    method: str,
) -> None:
    async def scenario() -> None:
        repository = _repository()
        gateway = _gateway()
        if method == "acquire_destination":
            repository.claim_next.side_effect = [COMMAND, None]
            repository.acquire_destination.side_effect = NetworkTimeout("private URI")
        else:
            getattr(repository, method).side_effect = [
                NetworkTimeout("private URI"),
                None,
            ]
        logger = Mock()
        sleeper = AsyncMock()
        await run_native_scheduling_cycle(
            _native(repository, gateway),
            policy=POLICY,
            logger=logger,
            sleeper=sleeper,
            jitter_source=lambda: 0.5,
        )
        sleeper.assert_awaited_once_with(1.0)
        gateway.schedule.assert_not_awaited()
        repository.mark_request_started.assert_not_awaited()
        events = [call.kwargs for call in logger.emit.call_args_list]
        assert events[0]["fields"]["failure_type"] == "NetworkTimeout"
        assert events[0]["fields"]["failure_category"] == "timeout"
        assert events[0]["fields"]["failure_module"] == "pymongo.errors"
        assert events[0]["fields"]["phase"] == (
            "reconcile" if method == "claim_reconciliation" else "execute"
        )
        assert events[1]["event_name"] == "native_schedule_cycle_recovered"
        assert "private URI" not in repr(events)

    asyncio.run(scenario())


def test_repeated_transport_failures_back_off_with_jitter_and_eventually_recover() -> (
    None
):
    async def scenario() -> None:
        native = AsyncMock(spec=RunNativeScheduling)
        native.execute_once.side_effect = [NetworkTimeout("hidden")] * 12 + [False]
        sleeper = AsyncMock()
        logger = Mock()
        times = iter(float(value) for value in range(13))
        await run_native_scheduling_cycle(
            native,
            policy=POLICY,
            logger=logger,
            sleeper=sleeper,
            jitter_source=lambda: 1.0,
            monotonic_clock=lambda: next(times),
        )
        delays = [call.args[0] for call in sleeper.await_args_list]
        assert delays == pytest.approx([1.2, 2.4, 4.8] + [8.0] * 9)
        assert native.execute_once.await_count == 13
        native.reconcile_once.assert_awaited_once()
        events = [call.kwargs for call in logger.emit.call_args_list]
        assert [event["fields"]["attempt"] for event in events[:-1]] == list(
            range(1, 13)
        )
        assert events[-1]["fields"] == {
            "attempts": 12,
            "recovery_elapsed_seconds": 12.0,
        }

    asyncio.run(scenario())


def test_connection_loss_then_floodwait_uses_server_minimum_not_backoff_cap() -> None:
    async def scenario() -> None:
        native = AsyncMock(spec=RunNativeScheduling)
        native.execute_once.side_effect = [
            ConnectionError("disconnected"),
            errors.TimeoutError(None),
            TelegramRateLimitError(90),
            False,
        ]
        sleeper = AsyncMock()
        await run_native_scheduling_cycle(
            native,
            policy=POLICY,
            logger=Mock(),
            sleeper=sleeper,
            jitter_source=lambda: 0.0,
        )
        assert [call.args[0] for call in sleeper.await_args_list] == [0.8, 1.6, 90.0]

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["execute_once", "reconcile_once"])
def test_permanent_cycle_failure_propagates_without_retry(method: str) -> None:
    async def scenario() -> None:
        native = AsyncMock(spec=RunNativeScheduling)
        error = OperationFailure("authentication failed", code=18)
        getattr(native, method).side_effect = error
        sleeper = AsyncMock()
        logger = Mock()
        with pytest.raises(OperationFailure) as failure:
            await run_native_scheduling_cycle(
                native, policy=POLICY, logger=logger, sleeper=sleeper
            )
        assert failure.value is error
        sleeper.assert_not_awaited()
        logger.emit.assert_not_called()

    asyncio.run(scenario())


def test_shutdown_cancels_retry_delay_without_another_cycle() -> None:
    async def scenario() -> None:
        native = AsyncMock(spec=RunNativeScheduling)
        native.execute_once.side_effect = NetworkTimeout("hidden")
        sleeping = asyncio.Event()
        stopped = asyncio.Event()

        async def sleeper(_delay: float) -> None:
            sleeping.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        task = asyncio.create_task(
            run_native_scheduling_cycle(
                native, policy=POLICY, logger=Mock(), sleeper=sleeper
            )
        )
        await asyncio.wait_for(sleeping.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
        native.execute_once.assert_awaited_once()
        native.reconcile_once.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("method", ["execute_once", "reconcile_once"])
def test_cycle_cancellation_is_never_retried(method: str) -> None:
    async def scenario() -> None:
        native = AsyncMock(spec=RunNativeScheduling)
        getattr(native, method).side_effect = asyncio.CancelledError()
        sleeper = AsyncMock()
        with pytest.raises(asyncio.CancelledError):
            await run_native_scheduling_cycle(
                native, policy=POLICY, logger=Mock(), sleeper=sleeper
            )
        sleeper.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("during_send", [False, True])
def test_permanent_operation_error_is_not_swallowed(during_send: bool) -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = COMMAND
        gateway = _gateway()
        error = errors.AuthKeyUnregisteredError(None)
        operation = gateway.schedule if during_send else gateway.list_scheduled
        operation.side_effect = error
        with pytest.raises(errors.AuthKeyUnregisteredError) as failure:
            await _native(repository, gateway).execute_once()
        assert failure.value is error
        assert repository.fail.await_args.kwargs["next_attempt_at"] is None
        assert repository.fail.await_args.kwargs["outcome_unknown"] is during_send
        repository.release_destination.assert_awaited_once()

    asyncio.run(scenario())


def test_permanent_reconciliation_error_is_not_rescheduled() -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_reconciliation.return_value = COMMAND
        gateway = _gateway()
        gateway.list_scheduled.side_effect = ValueError("invalid payload")
        with pytest.raises(ValueError, match="invalid payload"):
            await _native(repository, gateway).reconcile_once()
        repository.complete_reconciliation.assert_not_awaited()

    asyncio.run(scenario())


def test_floodwait_is_persisted_as_a_minimum_retry_time() -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = COMMAND
        repository.claim_reconciliation.return_value = COMMAND
        gateway = _gateway()
        gateway.list_scheduled.side_effect = TelegramRateLimitError(90)
        native = _native(repository, gateway)
        with pytest.raises(TelegramRateLimitError):
            await native.execute_once()
        assert repository.fail.await_args.kwargs["next_attempt_at"] == (
            NOW + timedelta(seconds=90)
        )
        assert not repository.fail.await_args.kwargs["outcome_unknown"]
        with pytest.raises(TelegramRateLimitError):
            await native.reconcile_once()
        assert repository.complete_reconciliation.await_args.kwargs[
            "next_check_at"
        ] == (NOW + timedelta(seconds=90))

    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["schedule", "cancel"])
def test_lost_request_started_write_ack_never_reopens_command(operation: str) -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = NativeScheduleCommand(
            "command",
            "post",
            -1001,
            3,
            NativeScheduleStatus.CLAIMED,
            telegram_message_ids=(81,) if operation == "cancel" else (),
            operation=operation,
        )
        repository.mark_request_started.side_effect = NetworkTimeout("write ack lost")
        gateway = _gateway()
        with pytest.raises(NetworkTimeout):
            await _native(repository, gateway).execute_once()
        assert repository.fail.await_args.kwargs["outcome_unknown"]
        assert repository.fail.await_args.kwargs["next_attempt_at"] is None
        gateway.schedule.assert_not_awaited()
        gateway.cancel.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize("cleanup", ["fail", "release_destination"])
def test_cleanup_timeout_does_not_hide_permanent_operation_error(cleanup: str) -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = COMMAND
        getattr(repository, cleanup).side_effect = NetworkTimeout("cleanup unavailable")
        gateway = _gateway()
        gateway.list_scheduled.side_effect = TypeError("programming error")
        with pytest.raises(TypeError, match="programming error"):
            await _native(repository, gateway).execute_once()

    asyncio.run(scenario())


def test_release_timeout_does_not_hide_cancellation() -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = COMMAND
        repository.release_destination.side_effect = NetworkTimeout(
            "cleanup unavailable"
        )
        gateway = _gateway()
        gateway.list_scheduled.side_effect = asyncio.CancelledError()
        with pytest.raises(asyncio.CancelledError):
            await _native(repository, gateway).execute_once()
        repository.fail.assert_not_awaited()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "policy", [RetryPolicy(1, 1, 1), RetryPolicy(2, 0, 1), RetryPolicy(2, 1, 0)]
)
def test_recovery_rejects_busy_loop_policies(policy: RetryPolicy) -> None:
    async def scenario() -> None:
        with pytest.raises(ValueError, match="positive retry delay"):
            await run_native_scheduling_cycle(
                cast("RunNativeScheduling", object()), policy=policy, logger=Mock()
            )

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["completion_write", "completion_callback"])
def test_reconciliation_completion_timeout_is_not_completed_twice(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def scenario() -> None:
        repository = _repository()
        command = NativeScheduleCommand(
            "scheduled",
            "post",
            -1001,
            3,
            NativeScheduleStatus.SCHEDULED,
            due_at=NOW + timedelta(minutes=5),
            telegram_message_ids=(81,),
        )
        repository.claim_reconciliation.side_effect = [command, None]
        native = _native(repository, _gateway())
        if stage == "completion_write":
            repository.complete_reconciliation.side_effect = NetworkTimeout("ack lost")
        else:
            monkeypatch.setattr(
                native,
                "_after_reconciled",
                AsyncMock(side_effect=NetworkTimeout("projection unavailable")),
            )
        sleeper = AsyncMock()
        await run_native_scheduling_cycle(
            native,
            policy=POLICY,
            logger=Mock(),
            sleeper=sleeper,
            jitter_source=lambda: 0.5,
        )
        repository.complete_reconciliation.assert_awaited_once()
        sleeper.assert_awaited_once_with(1.0)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "stage", ["claim_next", "mark_request_started", "complete_scheduled"]
)
def test_permanent_database_errors_propagate_from_real_use_case(stage: str) -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = COMMAND
        error = OperationFailure("unauthorized", code=13)
        getattr(repository, stage).side_effect = error
        sleeper = AsyncMock()
        with pytest.raises(OperationFailure) as failure:
            await run_native_scheduling_cycle(
                _native(repository, _gateway()),
                policy=POLICY,
                logger=Mock(),
                sleeper=sleeper,
            )
        assert failure.value is error
        sleeper.assert_not_awaited()

    asyncio.run(scenario())


def test_cancellation_does_not_send_after_command_ownership_is_lost() -> None:
    async def scenario() -> None:
        repository = _repository()
        repository.claim_next.return_value = NativeScheduleCommand(
            "cancel",
            "post",
            -1001,
            3,
            NativeScheduleStatus.CLAIMED,
            telegram_message_ids=(81,),
            operation="cancel",
        )
        repository.mark_request_started.return_value = False
        gateway = _gateway()
        with pytest.raises(RuntimeError, match="ownership was lost"):
            await _native(repository, gateway).execute_once()
        gateway.cancel.assert_not_awaited()

    asyncio.run(scenario())
