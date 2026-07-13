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
from telegram_assist_bot.application.ports import (
    ApprovalDeliveryRateLimitError,
    ApprovalDeliveryRejectedError,
    ApprovalDeliveryUnavailableError,
)
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
    from collections.abc import Awaitable, Callable
    from datetime import tzinfo

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
        NativeScheduleRepository,
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
    "immediate_queued": "در صف انتشار فوری",
    "scheduled": "زمان‌بندی شده",
    "publishing": "در حال انتشار",
    "published": "منتشر شد",
    "cancelled": "لغو شد",
    "permanent_failed": "انتشار ناموفق",
    "native_resolved": "دیگر در Scheduled Messages نیست",
    "native_outcome_unknown": "وضعیت زمان‌بندی نامشخص",
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
        historical_batch_pause_seconds: float = 10,
        max_attempts: int = 3,
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
        self._claimed_attempt_count = 0
        self._startup_at = clock().astimezone(UTC)
        self._historical_batch_size = max_backlog_per_startup
        self._historical_batch_successes = 0
        self._historical_batch_pause_seconds = historical_batch_pause_seconds
        self._historical_paused_until: datetime | None = None
        self._max_attempts = max_attempts
        self._idle = False

    async def execute_once(self) -> bool:
        """Contain every expected per-delivery failure and release its lease."""
        self._claimed_post_id = None
        self._claimed_attempt_count = 0
        try:
            return await self._execute_once()
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 - release every claimed post safely.
            return await self._release_claim_after_failure(error)
        finally:
            self._claimed_post_id = None
            self._claimed_attempt_count = 0

    async def _release_claim_after_failure(self, error: BaseException) -> bool:
        """Persist a safe retry state for a claimed delivery failure."""
        post_id = self._claimed_post_id
        if post_id is None:
            raise error
        category = getattr(error, "error_category", "transient")
        failure_type = type(error).__name__
        attempt_count = max(1, self._claimed_attempt_count)
        delay = self._retry_delay(attempt_count)
        if isinstance(error, ApprovalDeliveryRateLimitError):
            delay = min(float(error.retry_after_seconds), self._lease_seconds)
        terminal = attempt_count >= self._max_attempts
        next_attempt_at = self._clock().astimezone(UTC) + timedelta(seconds=delay)
        await self._operational.release_delivery(
            post_id,
            owner=self._owner,
            category=category,
            next_attempt_at=next_attempt_at,
            failure_type=failure_type,
            delivery_phase="proposal_loading",
            terminal=terminal,
        )
        self._emit(
            "approval_delivery_permanent_failed"
            if terminal
            else "approval_delivery_failed",
            approval_post_id=post_id,
            administrator_identifier="not_applicable",
            failure_category=category,
            failure_type=failure_type,
            delivery_phase="proposal_loading",
            content_kind="unknown",
            attempt_count=attempt_count,
            next_attempt_at=None if terminal else next_attempt_at,
            terminal=terminal,
        )
        return True

    async def _execute_once(self) -> bool:
        """Deliver one logical Post and retain successful per-admin progress."""
        now = self._clock().astimezone(UTC)
        # Live proposals always bypass historical pacing.
        claim = await self._operational.claim_ready(
            owner=self._owner,
            now=now,
            lease_until=now + timedelta(seconds=self._lease_seconds),
            ready_after=self._startup_at,
        )
        if claim is None and (
            self._historical_paused_until is None
            or now >= self._historical_paused_until
        ):
            claim = await self._operational.claim_ready(
                owner=self._owner,
                now=now,
                lease_until=now + timedelta(seconds=self._lease_seconds),
            )
        if claim is None:
            return False
        self._claimed_post_id = claim.post_id
        self._claimed_attempt_count = claim.attempt_count
        self._emit("approval_delivery_claimed", approval_post_id=claim.post_id)
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
            source_message_id=post.source_message_id,
            source_published_at=post.source_published_at,
            preview=post.content.text or post.content.caption,
            content_type=post.content_type,
            media_count=post.media_count,
            destination_summary="\n".join(
                f"📤 مقصد: {item.name}" for item in self._destinations
            ),
        )
        states = {item.administrator_id: item for item in claim.administrator_states}
        pending_retry_times: list[datetime] = []
        terminal_failures = 0
        for admin in self._administrators:
            reference_id = f"approval:{post.post_id}:{admin.telegram_user_id}"
            existing = await self._approvals.get_reference(reference_id)
            if existing is not None and existing.active:
                saved_state = states.get(admin.telegram_user_id)
                await self._record_administrator(
                    post.post_id,
                    admin.telegram_user_id,
                    status="completed",
                    attempt_count=saved_state.attempt_count
                    if saved_state is not None
                    else 0,
                    delivery_phase="completed",
                )
                continue
            state = states.get(admin.telegram_user_id)
            if state is not None and state.status == "permanent_failed":
                terminal_failures += 1
                continue
            if (
                state is not None
                and state.next_attempt_at is not None
                and state.next_attempt_at > now
            ):
                pending_retry_times.append(state.next_attempt_at)
                continue
            phase = "keyboard_rendering"
            try:
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
                        DestinationOption(item.destination_id, item.name)
                        for item in self._destinations
                    ),
                    selections=selections,
                    now=now,
                )
                phase = self._delivery_phase(existing)
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
                    approval_post_id=post.post_id,
                    administrator_identifier=admin.telegram_user_id,
                    content_kind=post.content_type,
                )
                if state is not None and state.attempt_count:
                    self._emit(
                        "approval_delivery_recovered",
                        approval_post_id=post.post_id,
                        administrator_identifier=admin.telegram_user_id,
                        delivery_phase=phase,
                        content_kind=post.content_type,
                        attempt_count=state.attempt_count,
                    )
                await self._record_administrator(
                    post.post_id,
                    admin.telegram_user_id,
                    status="completed",
                    attempt_count=state.attempt_count if state is not None else 0,
                    delivery_phase="completed",
                )
            except Exception as error:  # noqa: BLE001 - isolate each administrator.
                attempts = (state.attempt_count if state is not None else 0) + 1
                category = getattr(error, "error_category", "transient")
                terminal = (
                    isinstance(
                        error,
                        (
                            ApprovalDeliveryRejectedError,
                            ApprovalDeliveryUnavailableError,
                        ),
                    )
                    or attempts >= self._max_attempts
                )
                next_attempt = None
                if terminal:
                    terminal_failures += 1
                else:
                    retry_delay = self._retry_delay(attempts)
                    if isinstance(error, ApprovalDeliveryRateLimitError):
                        retry_delay = min(
                            float(error.retry_after_seconds), self._lease_seconds
                        )
                    next_attempt = now + timedelta(seconds=retry_delay)
                    pending_retry_times.append(next_attempt)
                failure_type = type(error).__name__
                await self._record_administrator(
                    post.post_id,
                    admin.telegram_user_id,
                    status="permanent_failed" if terminal else "retry",
                    attempt_count=attempts,
                    delivery_phase=phase,
                    next_attempt_at=next_attempt,
                    failure_category=category,
                    failure_type=failure_type,
                )
                event_name = (
                    "approval_delivery_permanent_failed"
                    if terminal
                    else "approval_delivery_failed"
                )
                self._emit(
                    event_name,
                    approval_post_id=post.post_id,
                    administrator_identifier=admin.telegram_user_id,
                    delivery_phase=phase,
                    content_kind=post.content_type,
                    attempt_count=attempts,
                    failure_category=category,
                    failure_type=failure_type,
                    next_attempt_at=next_attempt,
                    terminal=terminal,
                )
        if pending_retry_times:
            next_attempt_at = min(pending_retry_times)
            await self._operational.release_delivery(
                post.post_id,
                owner=self._owner,
                category="transient",
                next_attempt_at=next_attempt_at,
                delivery_phase="administrator_delivery",
            )
            self._emit(
                "approval_delivery_deferred",
                approval_post_id=post.post_id,
                next_attempt_at=next_attempt_at,
            )
            return True
        if terminal_failures:
            await self._operational.release_delivery(
                post.post_id,
                owner=self._owner,
                category="administrator_delivery",
                next_attempt_at=now,
                delivery_phase="administrator_delivery",
                terminal=True,
            )
            return True
        completed = await self._operational.complete_delivery(
            post.post_id, owner=self._owner
        )
        if (
            completed
            and claim.ready_at is not None
            and claim.ready_at <= self._startup_at
        ):
            self._historical_batch_successes += 1
            if self._historical_batch_successes >= self._historical_batch_size:
                self._historical_batch_successes = 0
                self._historical_paused_until = now + timedelta(
                    seconds=self._historical_batch_pause_seconds
                )
        self._emit("approval_delivery_completed", approval_post_id=post.post_id)
        return True

    async def _record_administrator(
        self,
        post_id: str,
        administrator_id: int,
        *,
        status: str,
        attempt_count: int,
        delivery_phase: str,
        next_attempt_at: datetime | None = None,
        failure_category: str | None = None,
        failure_type: str | None = None,
    ) -> None:
        """Persist isolated administrator progress when the repository supports it."""
        recorder = getattr(self._operational, "record_administrator_delivery", None)
        if recorder is not None:
            await recorder(
                post_id,
                administrator_id,
                owner=self._owner,
                status=status,
                attempt_count=attempt_count,
                delivery_phase=delivery_phase,
                next_attempt_at=next_attempt_at,
                failure_category=failure_category,
                failure_type=failure_type,
            )

    def _retry_delay(self, attempt_count: int) -> float:
        return float(
            min(
                self._retry_seconds * (2 ** max(0, attempt_count - 1)),
                max(self._retry_seconds, self._lease_seconds),
            )
        )

    @staticmethod
    def _delivery_phase(reference: ApprovalReference | None) -> str:
        if reference is None or reference.delivery_state.value in {
            "pending",
            "content_sending",
        }:
            return "content_message_send"
        if reference.delivery_state.value == "content_sent":
            return "reply_association"
        if reference.delivery_state.value == "control_sending":
            return "control_card_send"
        return "message_reference_persistence"

    def report_idle(self) -> None:
        """Emit one idle transition rather than one event per poll."""
        if not self._idle:
            self._idle = True
            self._emit("approval_delivery_worker_idle")

    def report_resumed(self) -> None:
        """Emit one resumed transition after idle polling finds work."""
        if self._idle:
            self._idle = False
            self._emit("approval_delivery_worker_resumed")

    def _emit(self, event_name: str, **fields: object) -> None:
        if self._logger is not None:
            level = LogLevel.INFO
            if event_name in {"approval_delivery_failed", "approval_delivery_deferred"}:
                level = LogLevel.WARNING
            elif event_name == "approval_delivery_permanent_failed":
                level = LogLevel.ERROR
            self._logger.emit(level=level, event_name=event_name, fields=fields)


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
        runtime_active: Callable[[], Awaitable[bool]] | None = None,
        timezone: tzinfo = UTC,
        logger: StructuredLogger | None = None,
        native_schedules: NativeScheduleRepository | None = None,
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
        self._runtime_active = runtime_active
        self._timezone = timezone
        self._logger = logger
        self._native_schedules = native_schedules

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
                failure_category=resolution.status.value,
            )
            return False
        if claims.destination_id is None or not await self._tokens.consume(data):
            await self._answer(update, INVALID_ACTION_TEXT, alert=True)
            self._emit(
                "approval_callback_rejected",
                administrator_id=update.actor_id,
                failure_category="replay",
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
            approval_post_id=claims.post_id,
            target_destination_id=claims.destination_id,
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
        if (
            previous is SelectionMode.SCHEDULED
            and current is not SelectionMode.SCHEDULED
            and self._native_schedules is not None
        ):
            command = await self._native_schedules.request_cancel_latest(
                post_id=post_id,
                destination_id=destination_id,
                now=now,
                follow_up_immediate=current is SelectionMode.IMMEDIATE,
            )
            if command is not None:
                await self._operational.record_destination_status(
                    post_id,
                    destination_id,
                    status="native_cancelling",
                    version=selection.version,
                    at=now,
                    action="scheduled",
                    due_at=command.due_at,
                )
                return
        legacy_cancel_allowed = not (
            previous is SelectionMode.SCHEDULED and self._native_schedules is not None
        )
        if (
            previous is not SelectionMode.NONE
            and previous is not current
            and legacy_cancel_allowed
        ):
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
                action="immediate",
                due_at=reservation.job.due_at,
            )
            self._emit(
                "publication_job_created",
                approval_post_id=post_id,
                target_destination_id=destination_id,
            )
        elif current is SelectionMode.SCHEDULED:
            if self._native_schedules is not None:
                await self._native_schedules.reserve(
                    post_id=post_id,
                    destination_id=destination_id,
                    selection_version=selection.version,
                    now=now,
                )
                await self._operational.record_destination_status(
                    post_id,
                    destination_id,
                    status="native_schedule_pending",
                    version=selection.version,
                    at=now,
                    action="scheduled",
                )
                self._emit(
                    "publication_job_created",
                    approval_post_id=post_id,
                    target_destination_id=destination_id,
                    publication_action="native_scheduled",
                )
                return
            reservation = await self._schedule.execute(
                ScheduleRequest(post_id, destination_id, True, True, True)
            )
            await self._operational.record_destination_status(
                post_id,
                destination_id,
                status="scheduled",
                version=selection.version,
                at=now,
                action="scheduled",
                due_at=reservation.job.due_at,
            )
            self._emit(
                "publication_job_created",
                approval_post_id=post_id,
                target_destination_id=destination_id,
            )
        else:
            if legacy_cancel_allowed:
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
        state_loader = getattr(self._operational, "destination_states", None)
        states = await state_loader(post_id) if state_loader is not None else {}
        runtime_active = (
            await self._runtime_active() if self._runtime_active is not None else False
        )
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
                    DestinationOption(item.destination_id, item.name)
                    for item in self._destinations
                ),
                selections=selections,
                now=now,
            )
            summaries: list[str] = []
            for item in self._destinations:
                status = statuses.get(item.destination_id)
                state = states.get(item.destination_id)
                if status is None:
                    summaries.append(
                        f"📤 مقصد: {item.name}\n⏳ وضعیت: در انتظار انتخاب"
                    )
                    continue
                if status == "immediate_queued":
                    activity = "فعال" if runtime_active else "غیرفعال"
                    occurred_at = state.occurred_at if state is not None else now
                    local = occurred_at.astimezone(self._timezone)
                    timezone_name = getattr(self._timezone, "key", str(self._timezone))
                    detail = (
                        f"در صف انتشار فوری — Runtime {activity}\n"
                        f"🕒 زمان ورود به صف: {local:%Y-%m-%d %H:%M:%S} "
                        f"{timezone_name}"
                    )
                elif status == "native_schedule_pending":
                    activity = "فعال" if runtime_active else "غیرفعال"
                    detail = f"در حال ثبت در Scheduled Messages — Runtime {activity}"
                elif status == "native_cancelling":
                    detail = "در حال حذف از Scheduled Messages"
                elif (
                    status in {"scheduled", "native_scheduled"}
                    and state is not None
                    and state.due_at is not None
                ):
                    local = state.due_at.astimezone(self._timezone)
                    timezone_name = getattr(self._timezone, "key", str(self._timezone))
                    activity = "فعال" if runtime_active else "غیرفعال"
                    detail = (
                        f"زمان‌بندی شده برای {local:%Y-%m-%d %H:%M:%S} "
                        f"{timezone_name} — Runtime {activity}"
                    )
                else:
                    detail = STATUS_LABELS.get(status, status)
                summaries.append(f"📤 مقصد: {item.name}\n⏳ وضعیت: {detail}")
            summary = "\n\n".join(summaries)
            header = self._header.execute(
                source_name=post.source_name,
                source_username=post.source_username,
                source_channel_id=post.source_channel_id,
                post_id=post.post_id,
                status="جزئیات هر مقصد در بالا",
                category=post.category,
                duplicate=post.duplicate,
                score=post.score,
                source_message_id=post.source_message_id,
                source_published_at=post.source_published_at,
                preview=post.content.text or post.content.caption,
                content_type=post.content_type,
                media_count=post.media_count,
                destination_summary=summary,
            )
            return header, keyboard

        await self._synchronize.execute(
            post_id=post_id, version=version, render=render, now=now
        )
        self._emit("approval_messages_synchronized", approval_post_id=post_id)

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
            self._emit("approval_sync_failed", approval_post_id=claim.post_id)
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

    def __init__(
        self,
        worker: ApprovalDeliveryWorker,
        *,
        poll_seconds: float,
        delivery_interval_seconds: float = 1,
    ) -> None:
        """Store one worker and its bounded idle polling interval."""
        self._worker = worker
        self._poll_seconds = poll_seconds
        self._delivery_interval_seconds = delivery_interval_seconds

    async def run(self) -> None:
        """Poll until cancellation and retain no work only in memory."""
        while True:
            worked = await self._worker.execute_once()
            if worked:
                reporter = getattr(self._worker, "report_resumed", None)
                if reporter is not None:
                    reporter()
                await asyncio.sleep(self._delivery_interval_seconds)
            else:
                reporter = getattr(self._worker, "report_idle", None)
                if reporter is not None:
                    reporter()
                await asyncio.sleep(self._poll_seconds)


__all__ = (
    "STATUS_LABELS",
    "ApprovalCallbackExecutor",
    "ApprovalDeliveryLoop",
    "ApprovalDeliveryWorker",
    "OperationalDestination",
)
