"""Non-live operational approval and publication orchestration tests."""

# ruff: noqa: E501

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from telegram_assist_bot.application.approvals import (
    AuthorizeAdminAction,
    BuildDestinationKeyboard,
    CallbackTokenService,
    DeliverApproval,
    RenderApprovalHeader,
    SynchronizeApprovalMessages,
    ToggleDestinationSelection,
)
from telegram_assist_bot.application.operational_approval import (
    ApprovalCallbackExecutor,
    ApprovalDeliveryLoop,
    ApprovalDeliveryWorker,
    OperationalDestination,
)
from telegram_assist_bot.application.ports import (
    ApprovalContent,
    ApprovalDeliveryClaim,
    ApprovalPost,
    ApprovalSyncClaim,
    BotEditOutcome,
    BotUpdate,
    DestinationPublicationState,
    OperationalApprovalRepository,
    ScheduleReservation,
)
from telegram_assist_bot.application.ports import (
    ScheduleRepository as ScheduleRepositoryPort,
)
from telegram_assist_bot.application.scheduling import CancelScheduledPost, SchedulePost
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    ApprovalDeliveryState,
    ApprovalReference,
    ApprovalSyncState,
    CallbackAction,
    CancellationPolicy,
    CancellationResult,
    ScheduledPublication,
    ScheduleStatus,
    SelectionMode,
    publication_identity,
    schedule_identity,
)
from telegram_assist_bot.presentation.bot.runtime_handlers import (
    AUTHORIZED_START_TEXT,
    OperationalBotHandlers,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    Redactor,
    StructuredEvent,
    StructuredLogger,
)
from tests.unit.application.approvals.test_admin_approval import MemoryRepository

NOW = datetime(2026, 7, 13, 12, tzinfo=UTC)
DESTINATION = -1001


class Gateway:
    """Record Bot operations without network access."""

    def __init__(self) -> None:
        self.sent: list[tuple[int, str]] = []
        self.answers: list[tuple[str, str, bool]] = []
        self.edits: list[tuple[str, object]] = []

    async def send_header(
        self,
        chat_id: int,
        text: str,
        keyboard: object = None,
        *,
        reply_to_message_id: int | None = None,
    ) -> int:
        del keyboard, reply_to_message_id
        self.sent.append((chat_id, text))
        return len(self.sent)

    async def send_content(
        self, chat_id: int, content: ApprovalContent
    ) -> tuple[int, ...]:
        del chat_id, content
        return (2,)

    async def edit_header(
        self, chat_id: int, message_id: int, text: str, keyboard: object
    ) -> BotEditOutcome:
        del chat_id, message_id
        self.edits.append((text, keyboard))
        return BotEditOutcome.UPDATED

    async def answer_callback(self, query_id: str, text: str, *, alert: bool) -> None:
        self.answers.append((query_id, text, alert))

    async def close(self) -> None:
        return None


class OperationalRepository:
    """Keep actionable and status state in memory."""

    def __init__(self) -> None:
        self.statuses: dict[int, str] = {}
        self.states: dict[int, DestinationPublicationState] = {}
        self.sync_version = 0
        self.sync_claim: ApprovalSyncClaim | None = None
        self.sync_completed = 0

    async def is_actionable(self, post_id: str) -> bool:
        return post_id == "post-1"

    async def record_destination_status(
        self,
        post_id: str,
        destination_id: int,
        *,
        status: str,
        version: int,
        at: datetime,
        action: str | None = None,
        due_at: datetime | None = None,
    ) -> None:
        del post_id, version
        self.statuses[destination_id] = status
        self.states[destination_id] = DestinationPublicationState(
            status, action, at, due_at
        )
        self.sync_version += 1

    async def destination_statuses(self, post_id: str) -> dict[int, str]:
        del post_id
        return dict(self.statuses)

    async def destination_states(
        self, post_id: str
    ) -> dict[int, DestinationPublicationState]:
        del post_id
        return dict(self.states)

    async def claim_sync(
        self, *, owner: str, now: datetime, lease_until: datetime
    ) -> ApprovalSyncClaim | None:
        del owner, now, lease_until
        claim = self.sync_claim
        self.sync_claim = None
        return claim

    async def complete_sync(self, post_id: str, *, owner: str, version: int) -> bool:
        del post_id, owner, version
        self.sync_completed += 1
        return True


class ScheduleRepository:
    """Record deterministic immediate and scheduled durable commands."""

    def __init__(self) -> None:
        self.jobs: dict[str, ScheduledPublication] = {}

    async def reserve_immediate(
        self, *, job_id: str, post_id: str, destination_id: int, now: datetime
    ) -> ScheduleReservation:
        created = job_id not in self.jobs
        self.jobs.setdefault(
            job_id,
            ScheduledPublication(
                job_id, post_id, destination_id, now, action="immediate"
            ),
        )
        return ScheduleReservation(self.jobs[job_id], created)

    async def reserve(
        self,
        *,
        job_id: str,
        post_id: str,
        destination_id: int,
        now: datetime,
        interval: timedelta,
    ) -> ScheduleReservation:
        created = job_id not in self.jobs
        self.jobs.setdefault(
            job_id,
            ScheduledPublication(job_id, post_id, destination_id, now + interval),
        )
        return ScheduleReservation(self.jobs[job_id], created)

    async def get(self, job_id: str) -> ScheduledPublication | None:
        return self.jobs.get(job_id)

    async def cancel(
        self,
        *,
        job_id: str,
        destination_id: int,
        expected_version: int,
        policy: CancellationPolicy,
        interval: timedelta,
        actor_id: int,
        now: datetime,
        correlation_id: str,
    ) -> CancellationResult:
        del destination_id, policy, interval, actor_id, now, correlation_id
        job = self.jobs.get(job_id)
        if job is None:
            return CancellationResult.NOT_FOUND
        if job.version != expected_version:
            return CancellationResult.CONFLICT
        self.jobs[job_id] = replace(
            job, status=ScheduleStatus.CANCELLED, version=job.version + 1
        )
        return CancellationResult.CANCELLED


class Loader:
    async def load(self, post_id: str) -> ApprovalPost:
        return ApprovalPost(
            post_id,
            "منبع",
            "source",
            -2001,
            ApprovalContent("سلام از متن اصلی", None),
            source_message_id=133594,
            source_published_at=NOW,
            content_type="text",
        )


class CallbackStub:
    def __init__(self) -> None:
        self.calls = 0

    async def execute(self, update: BotUpdate) -> bool:
        del update
        self.calls += 1
        return True


def administrator(user_id: int = 7) -> Administrator:
    return Administrator(
        user_id,
        True,
        "admin",
        frozenset({AdminPermission.VIEW, AdminPermission.TOGGLE}),
        frozenset({DESTINATION}),
    )


def test_start_authorizes_active_admin_and_denies_unknown_user() -> None:
    async def scenario() -> None:
        gateway = Gateway()
        handlers = OperationalBotHandlers(
            AuthorizeAdminAction((administrator(),)),
            gateway,
            cast("ApprovalCallbackExecutor", CallbackStub()),
        )
        assert await handlers.start(BotUpdate(7, 7, "private"))
        assert gateway.sent[-1][1] == AUTHORIZED_START_TEXT
        assert not await handlers.start(BotUpdate(9, 9, "private"))
        assert "مجاز" in gateway.sent[-1][1]
        assert "7" not in gateway.sent[-1][1]

    asyncio.run(scenario())


def test_valid_immediate_callback_creates_one_due_now_job_and_replay_is_harmless() -> (
    None
):
    async def scenario() -> None:
        executor, tokens, schedules, operational = build_executor()
        token = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        update = BotUpdate(7, 7, "private", token, "query-1")
        assert await executor.execute(update)
        assert not await executor.execute(update)
        identity = publication_identity("post-1", DESTINATION, "immediate")
        assert tuple(schedules.jobs) == (identity,)
        assert schedules.jobs[identity].due_at == NOW
        assert operational.statuses[DESTINATION] == "immediate_queued"
        assert operational.states[DESTINATION].due_at == NOW
        gateway = cast("Gateway", executor._gateway)
        assert "در صف انتشار فوری — Runtime غیرفعال" in gateway.edits[-1][0]
        assert "2026-07-13 15:30:00 Asia/Tehran" in gateway.edits[-1][0]
        assert "post-1" not in gateway.edits[-1][0]
        assert "133594" in gateway.edits[-1][0]
        keyboard = cast("Any", gateway.edits[-1][1])
        assert all("مقصد" in button.label for row in keyboard.rows for button in row)

    asyncio.run(scenario())


def test_scheduled_callback_uses_existing_slot_calculation_and_deselection_cancels() -> (
    None
):
    async def scenario() -> None:
        executor, tokens, schedules, operational = build_executor()
        first = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_SCHEDULED,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        assert await executor.execute(BotUpdate(7, 7, "private", first, "q1"))
        identity = schedule_identity("post-1", DESTINATION)
        assert schedules.jobs[identity].due_at == NOW + timedelta(seconds=300)
        gateway = cast("Gateway", executor._gateway)
        rendered = gateway.edits[-1][0]
        assert "مقصد" in rendered
        assert "2026-07-13 15:35:00 Asia/Tehran" in rendered
        second = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_SCHEDULED,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        assert await executor.execute(BotUpdate(7, 7, "private", second, "q2"))
        assert schedules.jobs[identity].status is ScheduleStatus.CANCELLED
        assert operational.statuses[DESTINATION] == "cancelled"

    asyncio.run(scenario())


def test_forged_and_destination_unauthorized_callbacks_have_no_side_effect() -> None:
    async def scenario() -> None:
        executor, tokens, schedules, operational = build_executor()
        assert not await executor.execute(BotUpdate(7, 7, "private", "c1_forged", "q"))
        token = await tokens.issue(
            actor_id=8,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        assert not await executor.execute(BotUpdate(8, 8, "private", token, "q2"))
        assert schedules.jobs == {}
        assert operational.statuses == {}

    asyncio.run(scenario())


def test_consumption_conflict_and_persistence_failures_return_safe_answers() -> None:
    async def scenario() -> None:
        executor, tokens, schedules, _ = build_executor()
        token = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )

        async def not_consumed(data: str) -> bool:
            del data
            return False

        tokens.consume = not_consumed  # type: ignore[assignment]
        assert not await executor.execute(BotUpdate(7, 7, "private", token, "replayed"))

        executor, tokens, schedules, _ = build_executor()
        approvals = cast("MemoryRepository", executor._approvals)
        approvals.force_conflict = True
        token = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        assert not await executor.execute(BotUpdate(7, 7, "private", token, "conflict"))

        executor, tokens, schedules, _ = build_executor()

        async def fail_reservation(**kwargs: object) -> ScheduleReservation:
            del kwargs
            raise RuntimeError

        schedules.reserve_immediate = fail_reservation  # type: ignore[method-assign]
        token = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_IMMEDIATE,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        assert not await executor.execute(BotUpdate(7, 7, "private", token, "failed"))

    asyncio.run(scenario())


def build_executor() -> tuple[
    ApprovalCallbackExecutor,
    CallbackTokenService,
    ScheduleRepository,
    OperationalRepository,
]:
    approvals = MemoryRepository()
    approvals.references["approval:post-1:7"] = ApprovalReference(
        "approval:post-1:7", 7, 7, "post-1", 10, (11,)
    )
    gateway = Gateway()
    admin = administrator()
    authorize = AuthorizeAdminAction((admin,))
    token_counter = 0

    def random_bytes(size: int) -> bytes:
        nonlocal token_counter
        token_counter += 1
        return token_counter.to_bytes(size, "big")

    tokens = CallbackTokenService(approvals, random_bytes)
    keyboard = BuildDestinationKeyboard(tokens)
    operational = OperationalRepository()
    schedules = ScheduleRepository()
    executor = ApprovalCallbackExecutor(
        tokens=tokens,
        authorize=authorize,
        approvals=approvals,
        operational=cast("OperationalApprovalRepository", operational),
        schedules=cast("ScheduleRepositoryPort", schedules),
        toggle=ToggleDestinationSelection(approvals, authorize),
        schedule=SchedulePost(
            cast("ScheduleRepositoryPort", schedules),
            clock=lambda: NOW,
            interval_seconds=300,
        ),
        cancel=CancelScheduledPost(
            cast("ScheduleRepositoryPort", schedules),
            clock=lambda: NOW,
            interval_seconds=300,
            policy=CancellationPolicy.PRESERVE,
        ),
        synchronize=SynchronizeApprovalMessages(gateway, approvals),
        keyboard=keyboard,
        gateway=gateway,
        loader=Loader(),
        header=RenderApprovalHeader(ZoneInfo("Asia/Tehran")),
        administrators=(admin,),
        destinations=(OperationalDestination(DESTINATION, "مقصد"),),
        clock=lambda: NOW,
        runtime_active=lambda: _false(),
        timezone=ZoneInfo("Asia/Tehran"),
    )
    return executor, tokens, schedules, operational


async def _false() -> bool:
    return False


class DeliveryOperational:
    def __init__(self) -> None:
        self.available = True
        self.completed = 0
        self.released = 0

    async def claim_ready(
        self,
        *,
        owner: str,
        now: datetime,
        lease_until: datetime,
        ready_after: datetime | None = None,
    ) -> ApprovalDeliveryClaim | None:
        del now, ready_after
        if not self.available:
            return None
        self.available = False
        return ApprovalDeliveryClaim("post-1", owner, lease_until)

    async def complete_delivery(self, post_id: str, *, owner: str) -> bool:
        del post_id, owner
        self.completed += 1
        return True

    async def release_delivery(
        self, post_id: str, *, owner: str, category: str, next_attempt_at: datetime
    ) -> bool:
        del post_id, owner, category, next_attempt_at
        self.released += 1
        return True


def test_delivery_worker_persists_each_admin_reference_and_completes_once() -> None:
    async def scenario() -> None:
        approvals = MemoryRepository()
        gateway = Gateway()
        operational = DeliveryOperational()
        admin = administrator()
        events: list[str] = []

        class Logger:
            def emit(self, *, event_name: str, **kwargs: object) -> None:
                del kwargs
                events.append(event_name)

        tokens = CallbackTokenService(approvals, lambda size: b"x" * size)
        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            Loader(),
            DeliverApproval(gateway, approvals),
            BuildDestinationKeyboard(tokens),
            RenderApprovalHeader(),
            (admin,),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
            max_backlog_per_startup=0,
            logger=cast("Any", Logger()),
        )
        assert await worker.execute_once()
        assert not await worker.execute_once()
        assert operational.completed == 1
        reference = approvals.references["approval:post-1:7"]
        assert reference.active
        assert reference.content_message_ids == (2,)
        assert "approval_delivery_completed" in events

    asyncio.run(scenario())


def test_delivery_worker_releases_transient_partial_delivery() -> None:
    class FailingGateway(Gateway):
        async def send_content(
            self, chat_id: int, content: ApprovalContent
        ) -> tuple[int, ...]:
            del chat_id, content
            raise TimeoutError

    async def scenario() -> None:
        approvals = MemoryRepository()
        operational = DeliveryOperational()
        gateway = FailingGateway()
        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            Loader(),
            DeliverApproval(gateway, approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"y" * size)
            ),
            RenderApprovalHeader(),
            (administrator(),),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
        )
        assert not await worker.execute_once()
        assert operational.released == 1
        reference = approvals.references["approval:post-1:7"]
        assert reference.delivery_state is ApprovalDeliveryState.CONTENT_SENDING
        assert not reference.content_message_ids

    asyncio.run(scenario())


def test_approval_delivery_uses_real_structured_logger_for_success_and_retry() -> None:
    class FailingGateway(Gateway):
        async def send_content(
            self, chat_id: int, content: ApprovalContent
        ) -> tuple[int, ...]:
            del chat_id, content
            raise TimeoutError

    async def scenario() -> None:
        events: list[dict[str, object]] = []

        def capture(event: StructuredEvent) -> None:
            events.append(dict(event))

        logger = StructuredLogger(
            sink=capture,
            clock=lambda: NOW,
            redactor=Redactor(secret_values=()),
            minimum_level=LogLevel.DEBUG,
        )

        def worker(
            operational: DeliveryOperational, gateway: Gateway
        ) -> ApprovalDeliveryWorker:
            approvals = MemoryRepository()
            return ApprovalDeliveryWorker(
                cast("OperationalApprovalRepository", operational),
                approvals,
                Loader(),
                DeliverApproval(gateway, approvals),
                BuildDestinationKeyboard(
                    CallbackTokenService(approvals, lambda size: b"r" * size)
                ),
                RenderApprovalHeader(),
                (administrator(),),
                (OperationalDestination(DESTINATION, "مقصد"),),
                owner="worker",
                clock=lambda: NOW,
                lease_seconds=30,
                retry_seconds=1,
                logger=logger,
            )

        successful = DeliveryOperational()
        assert await worker(successful, Gateway()).execute_once()
        assert successful.completed == 1

        retrying = DeliveryOperational()
        assert not await worker(retrying, FailingGateway()).execute_once()
        assert retrying.released == 1

        names = [event["event_name"] for event in events]
        assert "approval_delivery_claimed" in names
        assert "approval_message_delivered" in names
        assert "approval_delivery_completed" in names
        assert "approval_delivery_failed" in names
        reserved = {
            "timestamp",
            "level",
            "event_name",
            "correlation_id",
            "task_id",
            "job_id",
            "post_id",
            "channel_id",
            "destination_id",
            "admin_id",
            "error_type",
            "error_category",
            "error_message",
        }
        custom_keys = {
            key
            for event in events
            for key in event
            if key not in {"timestamp", "level", "event_name", "correlation_id"}
        }
        assert custom_keys.isdisjoint(reserved)
        assert all(event.get("approval_post_id") == "post-1" for event in events)

    asyncio.run(scenario())


def test_pending_synchronization_completes_and_idle_poll_is_harmless() -> None:
    async def scenario() -> None:
        executor, _, _, operational = build_executor()
        operational.sync_claim = ApprovalSyncClaim("post-1", 4, "sync")
        assert await executor.synchronize_pending_once(owner="sync", lease_seconds=30)
        assert operational.sync_completed == 1
        assert not await executor.synchronize_pending_once(
            owner="sync", lease_seconds=30
        )

    asyncio.run(scenario())


def test_delivery_resume_skips_existing_reference_and_loop_sleeps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        approvals = MemoryRepository()
        operational = DeliveryOperational()
        gateway = Gateway()
        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            Loader(),
            DeliverApproval(gateway, approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"z" * size)
            ),
            RenderApprovalHeader(),
            (administrator(),),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
        )
        assert await worker.execute_once()
        operational.available = True
        assert await worker.execute_once()
        assert len(gateway.sent) == 1

        class Idle:
            async def execute_once(self) -> bool:
                return False

        loop = ApprovalDeliveryLoop(cast("Any", Idle()), poll_seconds=1)

        async def stop(delay: float) -> None:
            del delay
            raise asyncio.CancelledError

        monkeypatch.setattr(asyncio, "sleep", stop)
        with pytest.raises(asyncio.CancelledError):
            await loop.run()

    asyncio.run(scenario())


def test_operational_edge_paths_remain_safe_and_retryable() -> None:
    async def scenario() -> None:
        executor, _, schedules, operational = build_executor()
        await executor._answer(BotUpdate(7, 7, "private"), "ok", alert=False)
        await executor._cancel_action(
            "missing", DESTINATION, SelectionMode.IMMEDIATE, 7, "correlation"
        )

        identity = publication_identity("post-1", DESTINATION, "immediate")
        schedules.jobs[identity] = ScheduledPublication(
            identity, "post-1", DESTINATION, NOW, action="immediate"
        )

        async def conflict(**kwargs: object) -> CancellationResult:
            del kwargs
            return CancellationResult.CONFLICT

        schedules.cancel = conflict  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="cancelled"):
            await executor._cancel_action(
                "post-1", DESTINATION, SelectionMode.IMMEDIATE, 7, "correlation"
            )

        reference = cast("MemoryRepository", executor._approvals).references[
            "approval:post-1:7"
        ]
        cast("MemoryRepository", executor._approvals).references[
            reference.reference_id
        ] = replace(reference, sync_state=ApprovalSyncState.RETRY)
        operational.sync_claim = ApprovalSyncClaim("post-1", 5, "sync")
        assert await executor.synchronize_pending_once(owner="sync", lease_seconds=30)
        assert operational.sync_completed == 0

        class Logger:
            def __init__(self) -> None:
                self.called = False

            def emit(self, **kwargs: object) -> None:
                del kwargs
                self.called = True

        logger = Logger()
        executor._logger = cast("Any", logger)
        executor._emit("safe_event", post_id="post-1")
        assert logger.called

    asyncio.run(scenario())
