"""Operational Aiogram polling and durable approval-delivery composition root."""

from __future__ import annotations

import asyncio
import secrets
from contextlib import suppress
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import uuid4

from aiogram import Dispatcher, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message  # noqa: TC002

from telegram_assist_bot.application.approvals import (
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
from telegram_assist_bot.application.ports import BotUpdate
from telegram_assist_bot.application.scheduling import CancelScheduledPost, SchedulePost
from telegram_assist_bot.bootstrap.admin_approval import (
    create_admin_approval_components,
)
from telegram_assist_bot.bootstrap.runtime import (
    FoundationExitCode,
    FoundationStartupError,
    create_foundation_application,
)
from telegram_assist_bot.domain import (
    Administrator,
    AdminPermission,
    CancellationPolicy,
)
from telegram_assist_bot.domain.categories import Category
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoApprovalPostLoader,
    MongoNativeScheduleRepository,
    MongoOperationalApprovalRepository,
    MongoRuntimeHeartbeatRepository,
    MongoScheduleRepository,
    initialize_native_schedule_indexes,
    initialize_operational_approval_indexes,
    initialize_publication_indexes,
)
from telegram_assist_bot.presentation.bot.runtime_handlers import OperationalBotHandlers
from telegram_assist_bot.shared.config import LogLevel

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from telegram_assist_bot.bootstrap.runtime import FoundationApplication
    from telegram_assist_bot.infrastructure.telegram.bot import (
        AiogramAdminMessagingGateway,
    )
    from telegram_assist_bot.shared.config import LoadedConfiguration
    from telegram_assist_bot.shared.observability import EventSink


class ApprovalBotStartupError(RuntimeError):
    """Report safe polling startup failure."""


class ApprovalBackgroundTaskStoppedError(RuntimeError):
    """Report a supervised approval worker that stopped without an exception."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _administrators(configuration: LoadedConfiguration) -> tuple[Administrator, ...]:
    settings = configuration.settings
    by_name = {
        item.name: item.telegram_channel_id for item in settings.destination_channels
    }
    return tuple(
        Administrator(
            item.telegram_user_id,
            item.active,
            item.role,
            frozenset(AdminPermission(value) for value in item.permissions),
            frozenset(item.allowed_destination_ids)
            or frozenset(by_name[name] for name in item.allowed_destination_names),
        )
        for item in settings.admins
    )


class ApprovalBotApplication:
    """Own MongoDB foundation, Aiogram Bot, delivery loop, and polling lifecycle."""

    def __init__(self, foundation: FoundationApplication) -> None:
        """Create an inert Bot API lifecycle without opening resources."""
        self._foundation = foundation
        self._dispatcher: Dispatcher | None = None
        self._polling_task: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._gateway: AiogramAdminMessagingGateway | None = None
        self._started = False
        self._closed = False

    async def start(
        self, configuration_path: Path, *, environ: Mapping[str, str]
    ) -> None:
        """Initialize repositories, handlers, delivery loops, and polling state."""
        try:
            await self._foundation.start(configuration_path, environ=environ)
            loaded = self._foundation.configuration
            settings = loaded.settings
            database = self._foundation.mongodb_client[settings.mongodb.database_name]
            components = await create_admin_approval_components(loaded, database)
            self._gateway = components.gateway
            deliveries = database["approval_deliveries"]
            schedules = database["scheduled_publications"]
            queues = database["schedule_queues"]
            publications = database["publications"]
            native_commands = database["native_schedule_commands"]
            native_leases = database["native_schedule_destination_leases"]
            await initialize_operational_approval_indexes(deliveries)
            await initialize_publication_indexes(publications, schedules, queues)
            await initialize_native_schedule_indexes(native_commands, native_leases)
            operational = MongoOperationalApprovalRepository(
                database["content_preparations"],
                deliveries,
                max_attempts=settings.telegram.bot.approval_retry_max_attempts,
            )
            heartbeat = MongoRuntimeHeartbeatRepository(database["runtime_heartbeats"])

            async def runtime_active() -> bool:
                return await heartbeat.is_active(
                    now=_utc_now(),
                    stale_after_seconds=max(
                        15.0, float(settings.publishing.worker_poll_seconds) * 3
                    ),
                )

            loader = MongoApprovalPostLoader(
                database["posts"],
                database["content_preparations"],
                database["media_items"],
                database["media_groups"],
                destination_names=tuple(
                    item.name for item in settings.destination_channels
                ),
                categories=tuple(
                    Category(c.category_id, c.display_name, c.active)
                    for c in settings.categorization.categories
                ),
            )
            schedule_repository = MongoScheduleRepository(schedules, queues)
            native_schedule_repository = MongoNativeScheduleRepository(
                native_commands, native_leases
            )
            administrators = _administrators(loaded)
            destinations = tuple(
                OperationalDestination(item.telegram_channel_id, item.name)
                for item in settings.destination_channels
                if item.enabled
            )
            tokens = CallbackTokenService(components.repository, secrets.token_bytes)
            keyboard = BuildDestinationKeyboard(tokens)
            synchronizer = SynchronizeApprovalMessages(
                components.gateway, components.repository
            )
            toggle = ToggleDestinationSelection(
                components.repository, components.authorize
            )
            schedule = SchedulePost(
                schedule_repository,
                clock=_utc_now,
                interval_seconds=settings.publishing.scheduled_publication_interval_seconds,
            )
            cancel = CancelScheduledPost(
                schedule_repository,
                clock=_utc_now,
                interval_seconds=settings.publishing.scheduled_publication_interval_seconds,
                policy=CancellationPolicy(settings.publishing.cancellation_policy),
            )
            header = RenderApprovalHeader(settings.timezone)
            callbacks = ApprovalCallbackExecutor(
                tokens=tokens,
                authorize=components.authorize,
                approvals=components.repository,
                operational=operational,
                schedules=schedule_repository,
                toggle=toggle,
                schedule=schedule,
                cancel=cancel,
                synchronize=synchronizer,
                keyboard=keyboard,
                gateway=components.gateway,
                loader=loader,
                header=header,
                administrators=administrators,
                destinations=destinations,
                clock=_utc_now,
                runtime_active=runtime_active,
                timezone=settings.timezone,
                logger=self._foundation.logger,
                native_schedules=native_schedule_repository,
            )
            handlers = OperationalBotHandlers(
                components.authorize, components.gateway, callbacks
            )
            router = Router(name="operational-approval")

            @router.message(CommandStart())
            async def start_handler(message: Message) -> None:
                actor_id = 0 if message.from_user is None else message.from_user.id
                await handlers.start(
                    BotUpdate(actor_id, message.chat.id, message.chat.type)
                )

            @router.callback_query()
            async def callback_handler(query: CallbackQuery) -> None:
                message = query.message
                if message is None:
                    await components.gateway.answer_callback(
                        query.id, "این عملیات معتبر نیست.", alert=True
                    )
                    return
                await handlers.callback(
                    BotUpdate(
                        query.from_user.id,
                        message.chat.id,
                        message.chat.type,
                        query.data,
                        query.id,
                    )
                )

            dispatcher = Dispatcher()
            dispatcher.include_router(router)
            self._dispatcher = dispatcher
            delivery = ApprovalDeliveryWorker(
                operational,
                components.repository,
                loader,
                DeliverApproval(components.gateway, components.repository),
                keyboard,
                header,
                administrators,
                destinations,
                owner=f"approval-bot-{uuid4().hex}",
                clock=_utc_now,
                lease_seconds=max(
                    float(settings.telegram.bot.approval_claim_lease_seconds),
                    float(
                        getattr(
                            settings.telegram.bot,
                            "approval_media_upload_timeout_seconds",
                            300,
                        )
                    )
                    + 30.0,
                ),
                retry_seconds=float(
                    settings.telegram.bot.approval_delivery_poll_seconds
                ),
                max_backlog_per_startup=(
                    settings.telegram.bot.approval_delivery_max_per_startup
                ),
                historical_batch_pause_seconds=float(
                    getattr(
                        settings.telegram.bot,
                        "approval_delivery_batch_pause_seconds",
                        10,
                    )
                ),
                max_attempts=settings.telegram.bot.approval_retry_max_attempts,
                logger=self._foundation.logger,
            )
            loop = ApprovalDeliveryLoop(
                delivery,
                poll_seconds=float(
                    settings.telegram.bot.approval_delivery_poll_seconds
                ),
                delivery_interval_seconds=float(
                    getattr(
                        settings.telegram.bot,
                        "approval_delivery_interval_seconds",
                        1,
                    )
                ),
            )
            self._delivery_task = asyncio.create_task(
                loop.run(), name="approval-delivery"
            )
            sync_owner = f"approval-sync-{uuid4().hex}"

            async def run_sync() -> None:
                while True:
                    worked = await callbacks.synchronize_pending_once(
                        owner=sync_owner,
                        lease_seconds=float(
                            settings.telegram.bot.approval_claim_lease_seconds
                        ),
                    )
                    if not worked:
                        await asyncio.sleep(
                            float(settings.telegram.bot.approval_delivery_poll_seconds)
                        )

            self._sync_task = asyncio.create_task(run_sync(), name="approval-sync")
            self._started = True
            self._foundation.logger.emit(
                level=LogLevel.INFO, event_name="approval_bot_started"
            )
        except asyncio.CancelledError:
            await self.shutdown()
            raise
        except Exception as error:
            await self.shutdown()
            raise ApprovalBotStartupError from error

    async def wait(self) -> None:
        """Run Aiogram long polling until cancellation or transport failure."""
        if not self._started or self._dispatcher is None or self._gateway is None:
            raise ApprovalBotStartupError
        settings = self._foundation.configuration.settings
        polling = asyncio.create_task(
            self._dispatcher.start_polling(
                self._gateway.bot,
                polling_timeout=settings.telegram.bot.polling_timeout_seconds,
                handle_signals=False,
            ),
            name="approval-polling",
        )
        self._polling_task = polling
        try:
            watched = {polling, self._delivery_task, self._sync_task}
            done, _ = await asyncio.wait(
                {task for task in watched if task is not None},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                if task is polling:
                    continue
                if task.cancelled():
                    error: BaseException = asyncio.CancelledError()
                else:
                    error = task.exception() or ApprovalBackgroundTaskStoppedError()
                self._foundation.logger.emit(
                    level=LogLevel.ERROR,
                    event_name="approval_background_task_failed",
                    fields={
                        "task_name": task.get_name(),
                        "failure_category": "transient",
                        "failure_type": type(error).__name__,
                    },
                )
                if not polling.done():
                    polling.cancel()
                await asyncio.gather(polling, return_exceptions=True)
                raise ApprovalBotStartupError from error
            await polling
        finally:
            if polling.done():
                await asyncio.gather(polling, return_exceptions=True)
                if self._polling_task is polling:
                    self._polling_task = None

    async def shutdown(self) -> None:
        """Stop polling and close Bot and MongoDB exactly once."""
        if self._closed:
            return
        self._closed = True
        if self._dispatcher is not None:
            with suppress(RuntimeError):
                await self._dispatcher.stop_polling()
        if self._polling_task is not None:
            if not self._polling_task.done():
                self._polling_task.cancel()
            await asyncio.gather(self._polling_task, return_exceptions=True)
            self._polling_task = None
        if self._delivery_task is not None:
            self._delivery_task.cancel()
            await asyncio.gather(self._delivery_task, return_exceptions=True)
            self._delivery_task = None
        if self._sync_task is not None:
            self._sync_task.cancel()
            await asyncio.gather(self._sync_task, return_exceptions=True)
            self._sync_task = None
        if self._gateway is not None:
            await self._gateway.close()
            self._gateway = None
        if self._foundation.is_ready:
            self._foundation.logger.emit(
                level=LogLevel.INFO, event_name="approval_bot_stopped"
            )
        await self._foundation.shutdown()


def create_approval_bot_application(*, sink: EventSink) -> ApprovalBotApplication:
    """Build one inert concrete approval Bot lifecycle."""
    return ApprovalBotApplication(create_foundation_application(sink=sink))


async def run_approval_bot_application(
    application: ApprovalBotApplication,
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
) -> FoundationExitCode:
    """Run polling with guaranteed idempotent cleanup and stable exit codes."""
    try:
        await application.start(configuration_path, environ=environ)
        await application.wait()
    except asyncio.CancelledError:
        await application.shutdown()
        raise
    except ApprovalBotStartupError as error:
        await application.shutdown()
        if isinstance(error.__cause__, FoundationStartupError):
            return error.__cause__.exit_code
        return FoundationExitCode.INFRASTRUCTURE_ERROR
    await application.shutdown()
    return FoundationExitCode.SUCCESS


__all__ = (
    "ApprovalBackgroundTaskStoppedError",
    "ApprovalBotApplication",
    "ApprovalBotStartupError",
    "create_approval_bot_application",
    "run_approval_bot_application",
)
