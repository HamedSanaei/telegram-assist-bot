"""Orchestrate durable Telegram-native scheduling over one shared User API client."""

from __future__ import annotations

import asyncio
from datetime import UTC, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.ports import NativeScheduleStatus

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime

    from telegram_assist_bot.application.ports import (
        NativeScheduleCommand,
        NativeScheduleRepository,
        PublicationPayloadLoader,
        TelegramNativeSchedulerGateway,
    )


class RunNativeScheduling:
    """Claim, serialize, and execute one native schedule or cancellation."""

    def __init__(
        self,
        repository: NativeScheduleRepository,
        gateway: TelegramNativeSchedulerGateway,
        loader: PublicationPayloadLoader,
        *,
        owner: str,
        clock: Callable[[], datetime],
        timeout_seconds: float,
        lease_seconds: float,
        retry_seconds: float,
        after_scheduled: Callable[[NativeScheduleCommand], Awaitable[None]]
        | None = None,
        after_cancelled: Callable[[NativeScheduleCommand], Awaitable[None]]
        | None = None,
        after_reconciled: Callable[[NativeScheduleCommand], Awaitable[None]]
        | None = None,
    ) -> None:
        """Store durable collaborators and bounded timing settings."""
        if lease_seconds <= timeout_seconds:
            raise ValueError(
                "Native scheduling lease must exceed its operation timeout."
            )
        self._repository = repository
        self._gateway = gateway
        self._loader = loader
        self._owner = owner
        self._clock = clock
        self._timeout_seconds = timeout_seconds
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds
        self._after_scheduled = after_scheduled
        self._after_cancelled = after_cancelled
        self._after_reconciled = after_reconciled

    async def execute_once(self) -> bool:
        """Execute one due command without ever publishing legacy scheduled jobs."""
        now = self._clock().astimezone(UTC)
        command = await self._repository.claim_next(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if command is None:
            return False
        acquired = await self._repository.acquire_destination(
            command.destination_id,
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if not acquired:
            await self._repository.fail(
                command.command_id,
                owner=self._owner,
                now=now,
                next_attempt_at=now + timedelta(seconds=self._retry_seconds),
                failure_type="NativeDestinationBusy",
                outcome_unknown=False,
            )
            return True
        request_started = False
        try:
            if command.operation == "cancel":
                if command.telegram_message_ids:
                    request_started = await self._repository.mark_request_started(
                        command.command_id, owner=self._owner
                    )
                    await self._gateway.cancel(
                        command.destination_id,
                        command.telegram_message_ids,
                        timeout_seconds=self._timeout_seconds,
                    )
                cancelled = await self._repository.complete_cancelled(
                    command.command_id, owner=self._owner
                )
                if self._after_cancelled is not None:
                    await self._after_cancelled(cancelled)
                return True

            existing = await self._gateway.list_scheduled(
                command.destination_id, timeout_seconds=self._timeout_seconds
            )
            latest = max(
                (item.due_at.astimezone(UTC) for item in existing), default=now
            )
            due_at = max(now, latest) + timedelta(minutes=5)
            request_started = await self._repository.mark_request_started(
                command.command_id, owner=self._owner, due_at=due_at
            )
            if not request_started:
                raise RuntimeError("Native scheduling command ownership was lost.")
            payload = await self._loader.load(command.post_id, command.destination_id)
            receipt = await self._gateway.schedule(
                payload, due_at=due_at, timeout_seconds=self._timeout_seconds
            )
            scheduled = await self._repository.complete_scheduled(
                command.command_id,
                owner=self._owner,
                receipt=receipt,
                now=self._clock().astimezone(UTC),
            )
            if (
                scheduled.status is NativeScheduleStatus.SCHEDULED
                and self._after_scheduled is not None
            ):
                await self._after_scheduled(scheduled)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - persist safe transport certainty.
            await self._repository.fail(
                command.command_id,
                owner=self._owner,
                now=self._clock().astimezone(UTC),
                next_attempt_at=(
                    None
                    if request_started
                    else now + timedelta(seconds=self._retry_seconds)
                ),
                failure_type=type(error).__name__,
                outcome_unknown=request_started,
            )
            return True
        finally:
            await self._repository.release_destination(
                command.destination_id, owner=self._owner
            )

    async def reconcile_once(self) -> bool:
        """Conservatively reconcile one persisted schedule with Telegram."""
        now = self._clock().astimezone(UTC)
        command = await self._repository.claim_reconciliation(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
        )
        if command is None:
            return False
        try:
            scheduled = await self._gateway.list_scheduled(
                command.destination_id, timeout_seconds=self._timeout_seconds
            )
            telegram = {item.message_id: item for item in scheduled}
            present = [
                telegram[value]
                for value in command.telegram_message_ids
                if value in telegram
            ]
            due_at: datetime | None
            if len(present) == len(command.telegram_message_ids):
                status = command.status
                due_at = max(item.due_at for item in present)
                next_check_at = min(
                    due_at,
                    now + timedelta(seconds=60),
                )
            elif present:
                status = NativeScheduleStatus.OUTCOME_UNKNOWN
                due_at = command.due_at
                next_check_at = None
            elif command.due_at is not None and now < command.due_at:
                status = NativeScheduleStatus.CANCELLED_EXTERNAL
                due_at = command.due_at
                next_check_at = None
            else:
                status = NativeScheduleStatus.RESOLVED
                due_at = command.due_at
                next_check_at = None
            reconciled = await self._repository.complete_reconciliation(
                command.command_id,
                owner=self._owner,
                status=status,
                due_at=due_at,
                next_check_at=next_check_at,
            )
            if self._after_reconciled is not None:
                await self._after_reconciled(reconciled)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - safe bounded reconciliation retry.
            del error
            await self._repository.complete_reconciliation(
                command.command_id,
                owner=self._owner,
                status=command.status,
                due_at=command.due_at,
                next_check_at=now + timedelta(seconds=self._retry_seconds),
            )
            return True


__all__ = ("RunNativeScheduling",)
