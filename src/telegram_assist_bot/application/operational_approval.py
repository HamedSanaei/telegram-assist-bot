"""Operational orchestration over existing approval and scheduling use cases."""

from __future__ import annotations

import asyncio
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from telegram_assist_bot.application.approvals import (
    EXPIRED_TEXT,
    INVALID_ACTION_TEXT,
    TEMPORARY_FAILURE_TEXT,
    BuildDestinationKeyboard,
    CallbackStatus,
    DeliverApproval,
    DestinationOption,
    RenderApprovalHeader,
    ToggleStatus,
)
from telegram_assist_bot.application.ports import ApprovalDeliveryRateLimitError
from telegram_assist_bot.application.scheduling import (
    CancelRequest,
    CancelScheduledPost,
    SchedulePost,
    ScheduleRequest,
)
from telegram_assist_bot.domain import (
    CallbackAction,
    CancellationResult,
    SelectionMode,
    publication_identity,
    schedule_identity,
)
from telegram_assist_bot.shared.config import LogLevel

if TYPE_CHECKING:
    from collections.abc import Callable

    from telegram_assist_bot.application.approvals import (
        AuthorizeAdminAction,
        CallbackTokenService,
        SynchronizeApprovalMessages,
        ToggleDestinationSelection,
    )
    from telegram_assist_bot.application.ports import (
        AdminMessagingGateway,
        ApprovalPostLoader,
        ApprovalRepository,
        BotUpdate,
        InlineKeyboard,
        OperationalApprovalRepository,
        ScheduleRepository,
    )
    from telegram_assist_bot.domain import (
        Administrator,
        ApprovalReference,
        DestinationSelection,
    )
    from telegram_assist_bot.shared.observability import StructuredLogger


STATUS_LABELS = {
    "immediate_queued": "فوری در صف",
    "scheduled": "زمان‌بندی‌شده",
    "published": "منتشر شد",
    "cancelled": "لغو شد",
    "permanent_failed": "خطای نهایی",
}


@dataclass(frozen=True, slots=True)
class OperationalDestination:
    """Describe one enabled configured destination."""

    destination_id: int
    name: str


class ApprovalDeliveryWorker:
    """Lease and deliver ready approvals independently to every active admin."""

    def __init__(
        self,
        operational: OperationalApprovalRepository,
        approvals: ApprovalRepository,
        loader: ApprovalPostLoader,
        deliver: DeliverApproval,
        keyboard: BuildDestinationKeyboard,
        header: RenderApprovalHeader,
        administrators: tuple[Administrator, ...],
        destinations: tuple[OperationalDestination, ...],
        *,
        owner: str,
        clock: Callable[[], datetime],
        lease_seconds: float,
        retry_seconds: float,
        max_backlog_per_startup: int = 10,
        logger: StructuredLogger | None = None,
    ) -> None:
        """Store durable delivery collaborators and bounded lease settings."""
        self._operational = operational
        self._approvals = approvals
        self._loader = loader
        self._deliver = deliver
        self._keyboard = keyboard
        self._header = header
        self._administrators = tuple(item for item in administrators if item.active)
        self._destinations = destinations
        self._owner = owner
        self._clock = clock
        self._lease_seconds = lease_seconds
        self._retry_seconds = retry_seconds
        self._logger = logger
        self._claimed_post_id: str | None = None
        self._startup_at = clock().astimezone(UTC)
        self._backlog_remaining = max_backlog_per_startup

    async def execute_once(self) -> bool:
        """Contain every expected per-delivery failure and release its lease."""
        self._claimed_post_id = None
        try:
            return await self._execute_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - release every claimed post safely.
            return await self._release_claim_after_failure(error)
        finally:
            self._claimed_post_id = None

    async def _release_claim_after_failure(self, error: BaseException) -> bool:
        """Persist a safe retry state for a claimed delivery failure."""
        post_id = self._claimed_post_id
        if post_id is None:
            raise error
        category = getattr(error, "error_category", "transient")
        delay = self._retry_seconds
        if isinstance(error, ApprovalDeliveryRateLimitError):
            delay = min(float(error.retry_after_seconds), self._lease_seconds)
        await self._operational.release_delivery(
            post_id,
            owner=self._owner,
            category=category,
            next_attempt_at=self._clock().astimezone(UTC) + timedelta(seconds=delay),
        )
        self._emit(
            "approval_delivery_failed",
            post_id=post_id,
            error_category=category,
        )
        return False

    async def _execute_once(self) -> bool:
        """Deliver one logical Post and retain successful per-admin progress."""
        now = self._clock().astimezone(UTC)
        claim_kwargs: dict[str, object] = {
            "owner": self._owner,
            "now": now,
            "lease_until": now + timedelta(seconds=self._lease_seconds),
        }
        if self._backlog_remaining == 0:
            claim_kwargs["ready_after"] = self._startup_at
        claim = await self._operational.claim_ready(**claim_kwargs)  # type: ignore[arg-type]
        if claim is None:
            return False
        self._claimed_post_id = claim.post_id
        if claim.ready_at is not None and claim.ready_at <= self._startup_at:
            self._backlog_remaining -= 1
        self._emit("approval_delivery_claimed", post_id=claim.post_id)
        post = await self._loader.load(claim.post_id)
        header = self._header.execute(
            source_name=post.source_name,
            source_username=post.source_username,
            source_channel_id=post.source_channel_id,
            post_id=post.post_id,
            status="آماده تأیید",
            category=post.category,
            duplicate=post.duplicate,
            score=post.score,
        )
        failed = False
        for admin in self._administrators:
            reference_id = f"approval:{post.post_id}:{admin.telegram_user_id}"
            existing = await self._approvals.get_reference(reference_id)
            if existing is not None and existing.active:
                continue
            selections = tuple(
                [
                    await self._approvals.get_selection(
                        post.post_id, item.destination_id
                    )
                    for item in self._destinations
                ]
            )
            keyboard = await self._keyboard.execute(
                actor=admin,
                post_id=post.post_id,
                destinations=tuple(
                    DestinationOption(item.destination_id)
                    for item in self._destinations
                ),
                selections=selections,
                now=now,
            )
            try:
                await self._deliver.execute(
                    reference_id=reference_id,
                    actor_id=admin.telegram_user_id,
                    post_id=post.post_id,
                    header=header,
                    content=post.content,
                    keyboard=keyboard,
                )
                self._emit(
                    "approval_message_delivered",
                    post_id=post.post_id,
                    administrator_id=admin.telegram_user_id,
                )
            except (TimeoutError, OSError, RuntimeError):
                failed = True
        if failed:
            await self._operational.release_delivery(
                post.post_id,
                owner=self._owner,
                category="transient",
                next_attempt_at=now + timedelta(seconds=self._retry_seconds),
            )
            self._emit("approval_delivery_failed", post_id=post.post_id)
            return False
        await self._operational.complete_delivery(post.post_id, owner=self._owner)
        self._emit("approval_delivery_completed", post_id=post.post_id)
        return True

    def _emit(self, event_name: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.emit(level=LogLevel.INFO, event_name=event_name, fields=fields)


class ApprovalCallbackExecutor:
    """Validate, atomically toggle, persist work, and fan out canonical UI state."""

    def __init__(
        self,
        *,
        tokens: CallbackTokenService,
        authorize: AuthorizeAdminAction,
        approvals: ApprovalRepository,
        operational: OperationalApprovalRepository,
        schedules: ScheduleRepository,
        toggle: ToggleDestinationSelection,
        schedule: SchedulePost,
        cancel: CancelScheduledPost,
        synchronize: SynchronizeApprovalMessages,
        keyboard: BuildDestinationKeyboard,
        gateway: AdminMessagingGateway,
        loader: ApprovalPostLoader,
        header: RenderApprovalHeader,
        administrators: tuple[Administrator, ...],
        destinations: tuple[OperationalDestination, ...],
        clock: Callable[[], datetime],
        logger: StructuredLogger | None = None,
    ) -> None:
        """Store existing approval, scheduling, and synchronization use cases."""
        self._tokens = tokens
        self._authorize = authorize
        self._approvals = approvals
        self._operational = operational
        self._schedules = schedules
        self._toggle = toggle
        self._schedule = schedule
        self._cancel = cancel
        self._synchronize = synchronize
        self._keyboard = keyboard
        self._gateway = gateway
        self._loader = loader
        self._header = header
        self._administrators = {item.telegram_user_id: item for item in administrators}
        self._destinations = destinations
        self._clock = clock
        self._logger = logger

    async def execute(self, update: BotUpdate) -> bool:
        """Handle one mapped callback without performing User API operations."""
        data = update.callback_data or ""
        now = self._clock().astimezone(UTC)
        actionable = False
        resolution = await self._tokens.resolve(data, actor_id=update.actor_id, now=now)
        if resolution.claims is not None:
            actionable = await self._operational.is_actionable(
                resolution.claims.post_id
            )
        resolution = await self._tokens.resolve_authorized(
            data,
            update=update,
            now=now,
            authorize=self._authorize,
            post_actionable=actionable,
        )
        claims = resolution.claims
        if resolution.status is not CallbackStatus.VALID or claims is None:
            message = (
                EXPIRED_TEXT
                if resolution.status is CallbackStatus.EXPIRED
                else INVALID_ACTION_TEXT
            )
            await self._answer(update, message, alert=True)
            self._emit(
                "approval_callback_rejected",
                administrator_id=update.actor_id,
                error_category=resolution.status.value,
            )
            return False
        if claims.destination_id is None or not await self._tokens.consume(data):
            await self._answer(update, INVALID_ACTION_TEXT, alert=True)
            self._emit(
                "approval_callback_rejected",
                administrator_id=update.actor_id,
                error_category="replay",
            )
            return False
        self._emit("approval_callback_authorized", administrator_id=update.actor_id)
        current = await self._approvals.get_selection(
            claims.post_id, claims.destination_id
        )
        requested = (
            SelectionMode.IMMEDIATE
            if claims.action is CallbackAction.TOGGLE_IMMEDIATE
            else SelectionMode.SCHEDULED
        )
        result = await self._toggle.execute(
            update,
            post_id=claims.post_id,
            destination_id=claims.destination_id,
            requested=requested,
            expected_version=current.version,
            post_actionable=True,
            now=now,
            correlation_id=uuid4().hex,
        )
        if result.status is not ToggleStatus.UPDATED or result.selection is None:
            await self._answer(update, TEMPORARY_FAILURE_TEXT, alert=True)
            return False
        updated = result.selection
        try:
            await self._persist_action(
                current.mode, updated.mode, updated, update.actor_id, now
            )
        except (PermissionError, RuntimeError, ValueError):
            await self._answer(update, TEMPORARY_FAILURE_TEXT, alert=True)
            return False
        await self._sync(claims.post_id, updated.version, now)
        self._emit(
            "approval_selection_changed",
            post_id=claims.post_id,
            destination_id=claims.destination_id,
        )
        await self._answer(update, "انتخاب ذخیره شد.", alert=False)
        return True

    async def _persist_action(
        self,
        previous: SelectionMode,
        current: SelectionMode,
        selection: DestinationSelection,
        actor_id: int,
        now: datetime,
    ) -> None:
        post_id = selection.post_id
        destination_id = selection.destination_id
        correlation_id = uuid4().hex
        if previous is not SelectionMode.NONE and previous is not current:
            await self._cancel_action(
                post_id, destination_id, previous, actor_id, correlation_id
            )
        if current is SelectionMode.IMMEDIATE:
            reservation = await self._schedules.reserve_immediate(
                job_id=publication_identity(post_id, destination_id, "immediate"),
                post_id=post_id,
                destination_id=destination_id,
                now=now,
            )
            await self._operational.record_destination_status(
                post_id,
                destination_id,
                status="immediate_queued",
                version=selection.version,
                at=now,
            )
            self._emit(
                "publication_job_created",
                post_id=post_id,
                destination_id=destination_id,
            )
        elif current is SelectionMode.SCHEDULED:
            reservation = await self._schedule.execute(
                ScheduleRequest(post_id, destination_id, True, True, True)
            )
            await self._operational.record_destination_status(
                post_id,
                destination_id,
                status="scheduled",
                version=selection.version,
                at=now,
            )
            self._emit(
                "publication_job_created",
                post_id=post_id,
                destination_id=destination_id,
            )
        else:
            await self._cancel_action(
                post_id, destination_id, previous, actor_id, correlation_id
            )
            await self._operational.record_destination_status(
                post_id,
                destination_id,
                status="cancelled",
                version=selection.version,
                at=now,
            )
            return
        del reservation

    async def _cancel_action(
        self,
        post_id: str,
        destination_id: int,
        mode: SelectionMode,
        actor_id: int,
        correlation_id: str,
    ) -> None:
        job_id = (
            publication_identity(post_id, destination_id, "immediate")
            if mode is SelectionMode.IMMEDIATE
            else schedule_identity(post_id, destination_id)
        )
        job = await self._schedules.get(job_id)
        if job is None:
            return
        result = await self._cancel.execute(
            CancelRequest(
                job_id, destination_id, job.version, actor_id, correlation_id, True
            )
        )
        if result not in {
            CancellationResult.CANCELLED,
            CancellationResult.ALREADY_CANCELLED,
            CancellationResult.ALREADY_COMPLETED,
        }:
            raise RuntimeError("Publication command could not be cancelled.")

    async def _sync(self, post_id: str, version: int, now: datetime) -> None:
        statuses = await self._operational.destination_statuses(post_id)
        post = await self._loader.load(post_id)

        async def render(reference: ApprovalReference) -> tuple[str, InlineKeyboard]:
            admin = self._administrators[reference.actor_id]
            selections = tuple(
                [
                    await self._approvals.get_selection(post_id, item.destination_id)
                    for item in self._destinations
                ]
            )
            keyboard = await self._keyboard.execute(
                actor=admin,
                post_id=post_id,
                destinations=tuple(
                    DestinationOption(item.destination_id)
                    for item in self._destinations
                ),
                selections=selections,
                now=now,
            )
            summary = "، ".join(
                f"{item.name}: {STATUS_LABELS[statuses[item.destination_id]]}"
                for item in self._destinations
                if item.destination_id in statuses
            )
            header = self._header.execute(
                source_name=post.source_name,
                source_username=post.source_username,
                source_channel_id=post.source_channel_id,
                post_id=post.post_id,
                status=summary or "در انتظار انتخاب",
                category=post.category,
                duplicate=post.duplicate,
                score=post.score,
            )
            return header, keyboard

        await self._synchronize.execute(
            post_id=post_id, version=version, render=render, now=now
        )
        self._emit("approval_messages_synchronized", post_id=post_id)

    async def synchronize_pending_once(
        self, *, owner: str, lease_seconds: float
    ) -> bool:
        """Retry one durable status synchronization after callback or restart."""
        now = self._clock().astimezone(UTC)
        claim = await self._operational.claim_sync(
            owner=owner,
            now=now,
            lease_until=now + timedelta(seconds=lease_seconds),
        )
        if claim is None:
            return False
        await self._sync(claim.post_id, claim.version, now)
        references = await self._approvals.list_active_references(claim.post_id)
        if all(item.sync_state.value == "current" for item in references):
            await self._operational.complete_sync(
                claim.post_id, owner=owner, version=claim.version
            )
        else:
            self._emit("approval_sync_failed", post_id=claim.post_id)
        return True

    async def _answer(self, update: BotUpdate, text: str, *, alert: bool) -> None:
        if update.callback_query_id:
            with suppress(TimeoutError):
                await self._gateway.answer_callback(
                    update.callback_query_id, text, alert=alert
                )

    def _emit(self, event_name: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.emit(level=LogLevel.INFO, event_name=event_name, fields=fields)


class ApprovalDeliveryLoop:
    """Run bounded delivery polling until cancellation."""

    def __init__(self, worker: ApprovalDeliveryWorker, *, poll_seconds: float) -> None:
        """Store one worker and its bounded idle polling interval."""
        self._worker = worker
        self._poll_seconds = poll_seconds

    async def run(self) -> None:
        """Poll until cancellation and retain no work only in memory."""
        while True:
            worked = await self._worker.execute_once()
            if not worked:
                await asyncio.sleep(self._poll_seconds)


__all__ = (
    "STATUS_LABELS",
    "ApprovalCallbackExecutor",
    "ApprovalDeliveryLoop",
    "ApprovalDeliveryWorker",
    "OperationalDestination",
)
