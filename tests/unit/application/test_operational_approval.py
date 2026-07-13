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
    ApprovalAdministratorDeliveryState,
    ApprovalContent,
    ApprovalDeliveryClaim,
    ApprovalDeliveryRateLimitError,
    ApprovalDeliveryUnavailableError,
    ApprovalPost,
    ApprovalSyncClaim,
    BotEditOutcome,
    BotUpdate,
    DestinationPublicationState,
    NativeScheduleCommand,
    NativeScheduleStatus,
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


def test_scheduled_callback_persists_native_command_without_legacy_schedule() -> None:
    class NativeRepository:
        def __init__(self) -> None:
            self.command: NativeScheduleCommand | None = None

        async def reserve(self, **kwargs: object) -> NativeScheduleCommand:
            self.command = NativeScheduleCommand(
                "native",
                str(kwargs["post_id"]),
                cast("int", kwargs["destination_id"]),
                cast("int", kwargs["selection_version"]),
                NativeScheduleStatus.PENDING,
            )
            return self.command

        async def request_cancel_latest(
            self, **kwargs: object
        ) -> NativeScheduleCommand | None:
            del kwargs
            return self.command

    async def scenario() -> None:
        executor, tokens, schedules, operational = build_executor()
        native = NativeRepository()
        executor._native_schedules = cast("Any", native)
        token = await tokens.issue(
            actor_id=7,
            action=CallbackAction.TOGGLE_SCHEDULED,
            post_id="post-1",
            destination_id=DESTINATION,
            now=NOW,
        )
        assert await executor.execute(BotUpdate(7, 7, "private", token, "native"))
        assert native.command is not None
        assert schedules.jobs == {}
        assert operational.statuses[DESTINATION] == "native_schedule_pending"
        gateway = cast("Gateway", executor._gateway)
        assert "در حال ثبت در Scheduled Messages" in gateway.edits[-1][0]

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
        self.administrator_results: list[dict[str, object]] = []

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
        self,
        post_id: str,
        *,
        owner: str,
        category: str,
        next_attempt_at: datetime,
        failure_type: str | None = None,
        delivery_phase: str | None = None,
        terminal: bool = False,
    ) -> bool:
        del (
            post_id,
            owner,
            category,
            next_attempt_at,
            failure_type,
            delivery_phase,
            terminal,
        )
        self.released += 1
        return True

    async def record_administrator_delivery(
        self,
        post_id: str,
        administrator_id: int,
        **values: object,
    ) -> bool:
        self.administrator_results.append(
            {"post_id": post_id, "administrator_id": administrator_id, **values}
        )
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
        assert await worker.execute_once()
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
        assert await worker(retrying, FailingGateway()).execute_once()
        assert retrying.released == 1

        names = [event["event_name"] for event in events]
        assert "approval_delivery_claimed" in names
        assert "approval_message_delivered" in names
        assert "approval_delivery_completed" in names
        assert "approval_delivery_failed" in names
        failure = next(
            event
            for event in events
            if event["event_name"] == "approval_delivery_failed"
        )
        assert failure["level"] == LogLevel.WARNING.value
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


def test_failed_retries_do_not_consume_historical_success_budget() -> None:
    class QueueOperational(DeliveryOperational):
        def __init__(self) -> None:
            super().__init__()
            self.claims = [
                ("failing", NOW - timedelta(minutes=3)),
                ("failing", NOW - timedelta(minutes=3)),
                ("failing", NOW - timedelta(minutes=3)),
                ("healthy", NOW - timedelta(minutes=2)),
                ("new", NOW + timedelta(seconds=1)),
            ]
            self.ready_filters: list[datetime | None] = []

        async def claim_ready(
            self,
            *,
            owner: str,
            now: datetime,
            lease_until: datetime,
            ready_after: datetime | None = None,
        ) -> ApprovalDeliveryClaim | None:
            del now
            self.ready_filters.append(ready_after)
            for index, (post_id, ready_at) in enumerate(self.claims):
                if ready_after is None or ready_at > ready_after:
                    self.claims.pop(index)
                    return ApprovalDeliveryClaim(post_id, owner, lease_until, ready_at)
            return None

    class QueueLoader(Loader):
        async def load(self, post_id: str) -> ApprovalPost:
            post = await super().load(post_id)
            return replace(
                post,
                content=ApprovalContent("fail" if post_id == "failing" else "ok", None),
            )

    class SelectiveGateway(Gateway):
        async def send_content(
            self, chat_id: int, content: ApprovalContent
        ) -> tuple[int, ...]:
            if content.text == "fail":
                raise TimeoutError
            return await super().send_content(chat_id, content)

    async def scenario() -> None:
        approvals = MemoryRepository()
        operational = QueueOperational()
        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            QueueLoader(),
            DeliverApproval(SelectiveGateway(), approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"q" * size)
            ),
            RenderApprovalHeader(),
            (administrator(),),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
            max_backlog_per_startup=1,
        )
        for _ in range(5):
            assert await worker.execute_once()
        assert operational.completed == 2
        assert operational.ready_filters == [
            NOW,
            NOW,
            None,
            NOW,
            None,
            NOW,
            None,
            NOW,
            None,
        ]

    asyncio.run(scenario())


def test_per_administrator_failure_preserves_other_successful_reference() -> None:
    class OneAdminFails(Gateway):
        async def send_content(
            self, chat_id: int, content: ApprovalContent
        ) -> tuple[int, ...]:
            if chat_id == 7:
                raise TimeoutError
            return await super().send_content(chat_id, content)

    async def scenario() -> None:
        approvals = MemoryRepository()
        operational = DeliveryOperational()
        gateway = OneAdminFails()
        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            Loader(),
            DeliverApproval(gateway, approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"a" * size)
            ),
            RenderApprovalHeader(),
            (administrator(7), administrator(8)),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
        )
        assert await worker.execute_once()
        assert approvals.references["approval:post-1:8"].active
        operational.available = True
        assert await worker.execute_once()
        assert len([item for item in gateway.sent if item[0] == 8]) == 1

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "content_kind", ["text", "photo", "video", "document", "album"]
)
def test_content_kind_failures_are_isolated_and_safely_diagnosed(
    content_kind: str,
) -> None:
    class KindLoader(Loader):
        async def load(self, post_id: str) -> ApprovalPost:
            return replace(await super().load(post_id), content_type=content_kind)

    class FailingGateway(Gateway):
        async def send_content(
            self, chat_id: int, content: ApprovalContent
        ) -> tuple[int, ...]:
            del chat_id, content
            raise TimeoutError

    async def scenario() -> None:
        events: list[dict[str, object]] = []
        approvals = MemoryRepository()
        logger = StructuredLogger(
            sink=lambda event: events.append(dict(event)),
            clock=lambda: NOW,
            redactor=Redactor(secret_values=()),
            minimum_level=LogLevel.DEBUG,
        )
        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", DeliveryOperational()),
            approvals,
            KindLoader(),
            DeliverApproval(FailingGateway(), approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"k" * size)
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
        assert await worker.execute_once()
        failure = next(
            event
            for event in events
            if event["event_name"] == "approval_delivery_failed"
        )
        assert failure["content_kind"] == content_kind
        assert failure["delivery_phase"] == "content_message_send"
        assert "error_message" not in failure

    asyncio.run(scenario())


def test_proposal_loading_failures_are_bounded_and_preserve_cancellation() -> None:
    class LoadingFailure(Loader):
        async def load(self, post_id: str) -> ApprovalPost:
            del post_id
            raise ApprovalDeliveryRateLimitError(90)

    class AttemptOperational(DeliveryOperational):
        def __init__(self, attempt_count: int) -> None:
            super().__init__()
            self.attempt_count = attempt_count
            self.terminal = False

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
            return ApprovalDeliveryClaim(
                "post-1", owner, lease_until, NOW, self.attempt_count
            )

        async def release_delivery(
            self,
            post_id: str,
            *,
            owner: str,
            category: str,
            next_attempt_at: datetime,
            failure_type: str | None = None,
            delivery_phase: str | None = None,
            terminal: bool = False,
        ) -> bool:
            self.terminal = terminal
            return await super().release_delivery(
                post_id,
                owner=owner,
                category=category,
                next_attempt_at=next_attempt_at,
                failure_type=failure_type,
                delivery_phase=delivery_phase,
                terminal=terminal,
            )

    def worker(
        operational: AttemptOperational, loader: Loader
    ) -> ApprovalDeliveryWorker:
        approvals = MemoryRepository()
        return ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            loader,
            DeliverApproval(Gateway(), approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"l" * size)
            ),
            RenderApprovalHeader(),
            (administrator(),),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
            max_attempts=3,
        )

    async def scenario() -> None:
        retrying = AttemptOperational(1)
        assert await worker(retrying, LoadingFailure()).execute_once()
        assert not retrying.terminal
        terminal = AttemptOperational(3)
        assert await worker(terminal, LoadingFailure()).execute_once()
        assert terminal.terminal

        class CancelledLoader(Loader):
            async def load(self, post_id: str) -> ApprovalPost:
                del post_id
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await worker(AttemptOperational(1), CancelledLoader()).execute_once()

        class BrokenClaim(AttemptOperational):
            async def claim_ready(
                self, **kwargs: object
            ) -> ApprovalDeliveryClaim | None:
                del kwargs
                raise RuntimeError("safe synthetic failure")

        with pytest.raises(RuntimeError, match="safe synthetic"):
            await worker(BrokenClaim(1), Loader()).execute_once()

    asyncio.run(scenario())


def test_deferred_terminal_recovered_and_idle_delivery_paths() -> None:
    class StatefulOperational(DeliveryOperational):
        def __init__(self, state: ApprovalAdministratorDeliveryState) -> None:
            super().__init__()
            self.state = state
            self.terminal = False

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
            return ApprovalDeliveryClaim(
                "post-1", owner, lease_until, NOW, 1, (self.state,)
            )

        async def release_delivery(
            self,
            post_id: str,
            *,
            owner: str,
            category: str,
            next_attempt_at: datetime,
            failure_type: str | None = None,
            delivery_phase: str | None = None,
            terminal: bool = False,
        ) -> bool:
            self.terminal = terminal
            return await super().release_delivery(
                post_id,
                owner=owner,
                category=category,
                next_attempt_at=next_attempt_at,
                failure_type=failure_type,
                delivery_phase=delivery_phase,
                terminal=terminal,
            )

    class RateLimitedGateway(Gateway):
        async def send_content(
            self, chat_id: int, content: ApprovalContent
        ) -> tuple[int, ...]:
            del chat_id, content
            raise ApprovalDeliveryRateLimitError(2)

    async def run_case(
        state: ApprovalAdministratorDeliveryState, gateway: Gateway
    ) -> tuple[StatefulOperational, list[str], ApprovalDeliveryWorker]:
        operational = StatefulOperational(state)
        approvals = MemoryRepository()
        events: list[str] = []

        class Logger:
            def emit(self, *, event_name: str, **kwargs: object) -> None:
                del kwargs
                events.append(event_name)

        worker = ApprovalDeliveryWorker(
            cast("OperationalApprovalRepository", operational),
            approvals,
            Loader(),
            DeliverApproval(gateway, approvals),
            BuildDestinationKeyboard(
                CallbackTokenService(approvals, lambda size: b"s" * size)
            ),
            RenderApprovalHeader(),
            (administrator(),),
            (OperationalDestination(DESTINATION, "مقصد"),),
            owner="worker",
            clock=lambda: NOW,
            lease_seconds=30,
            retry_seconds=1,
            logger=cast("Any", Logger()),
        )
        assert await worker.execute_once()
        return operational, events, worker

    async def scenario() -> None:
        future = ApprovalAdministratorDeliveryState(
            7, "retry", 1, NOW + timedelta(seconds=10)
        )
        deferred, _, _ = await run_case(future, Gateway())
        assert not deferred.terminal

        permanent = ApprovalAdministratorDeliveryState(7, "permanent_failed", 3)
        terminal, _, _ = await run_case(permanent, Gateway())
        assert terminal.terminal

        recovered_state = ApprovalAdministratorDeliveryState(7, "retry", 1, NOW)
        _, events, recovered = await run_case(recovered_state, Gateway())
        assert "approval_delivery_recovered" in events
        recovered.report_idle()
        recovered.report_idle()
        recovered.report_resumed()
        recovered.report_resumed()
        assert events.count("approval_delivery_worker_idle") == 1
        assert events.count("approval_delivery_worker_resumed") == 1

        rate_state = ApprovalAdministratorDeliveryState(7, "retry", 1, NOW)
        _, rate_events, _ = await run_case(rate_state, RateLimitedGateway())
        assert "approval_delivery_failed" in rate_events

        unavailable = ApprovalAdministratorDeliveryState(7, "retry", 1, NOW)

        class UnavailableGateway(Gateway):
            async def send_content(
                self, chat_id: int, content: ApprovalContent
            ) -> tuple[int, ...]:
                del chat_id, content
                raise ApprovalDeliveryUnavailableError

        unavailable_result, unavailable_events, _ = await run_case(
            unavailable, UnavailableGateway()
        )
        assert unavailable_result.terminal
        assert "approval_delivery_permanent_failed" in unavailable_events

    asyncio.run(scenario())


def test_delivery_phase_diagnostics_cover_restart_states() -> None:
    base = ApprovalReference("ref", 7, 7, "post", 0, (), active=False)
    assert ApprovalDeliveryWorker._delivery_phase(None) == "content_message_send"
    assert (
        ApprovalDeliveryWorker._delivery_phase(
            replace(base, delivery_state=ApprovalDeliveryState.CONTENT_SENT)
        )
        == "reply_association"
    )
    assert (
        ApprovalDeliveryWorker._delivery_phase(
            replace(base, delivery_state=ApprovalDeliveryState.CONTROL_SENDING)
        )
        == "control_card_send"
    )
    assert (
        ApprovalDeliveryWorker._delivery_phase(
            replace(base, delivery_state=ApprovalDeliveryState.COMPLETED)
        )
        == "message_reference_persistence"
    )


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
