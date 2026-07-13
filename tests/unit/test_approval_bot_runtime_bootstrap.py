"""Non-live lifecycle tests for the operational approval Bot composition."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from zoneinfo import ZoneInfo

import pytest

import telegram_assist_bot.bootstrap.approval_bot as module
from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.application.ports import BotEditOutcome
from telegram_assist_bot.bootstrap.runtime import (
    FoundationConfigurationError,
    FoundationExitCode,
)
from telegram_assist_bot.domain import Administrator, AdminPermission
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import (
    Redactor,
    StructuredEvent,
    StructuredLogger,
)
from tests.unit.application.approvals.test_admin_approval import MemoryRepository

if TYPE_CHECKING:
    from collections.abc import Callable


class Logger:
    def __init__(self) -> None:
        self.events: list[str] = []

    def emit(self, *, event_name: str, **kwargs: object) -> None:
        del kwargs
        self.events.append(event_name)


class Gateway:
    def __init__(self) -> None:
        self.bot = object()
        self.closed = 0

    async def close(self) -> None:
        self.closed += 1

    async def send_header(
        self,
        chat_id: int,
        text: str,
        keyboard: object = None,
        *,
        reply_to_message_id: int | None = None,
    ) -> int:
        del chat_id, text, keyboard, reply_to_message_id
        return 1

    async def send_content(self, chat_id: int, content: object) -> tuple[int, ...]:
        del chat_id, content
        return (2,)

    async def edit_header(
        self, chat_id: int, message_id: int, text: str, keyboard: object
    ) -> BotEditOutcome:
        del chat_id, message_id, text, keyboard
        return BotEditOutcome.UPDATED

    async def answer_callback(self, query_id: str, text: str, *, alert: bool) -> None:
        del query_id, text, alert


class Database(dict[str, object]):
    def __missing__(self, key: str) -> object:
        value = object()
        self[key] = value
        return value


class Foundation:
    def __init__(self) -> None:
        admin = SimpleNamespace(
            telegram_user_id=7,
            active=True,
            role="admin",
            permissions=("approval.view", "approval.toggle"),
            allowed_destination_ids=(-1001,),
            allowed_destination_names=("dest",),
        )
        destination = SimpleNamespace(
            name="dest", telegram_channel_id=-1001, enabled=True
        )
        bot = SimpleNamespace(
            polling_timeout_seconds=30,
            approval_claim_lease_seconds=60,
            approval_delivery_poll_seconds=5,
            approval_delivery_max_per_startup=10,
            approval_retry_max_attempts=3,
        )
        publishing = SimpleNamespace(
            scheduled_publication_interval_seconds=300,
            cancellation_policy="preserve",
        )
        self.configuration = SimpleNamespace(
            settings=SimpleNamespace(
                mongodb=SimpleNamespace(database_name="test"),
                telegram=SimpleNamespace(bot=bot),
                admins=(admin,),
                destination_channels=(destination,),
                publishing=publishing,
                timezone=ZoneInfo("Asia/Tehran"),
            )
        )
        self.mongodb_client = {"test": Database()}
        self.logger = Logger()
        self.is_ready = False
        self.closed = 0

    async def start(self, path: object, *, environ: object) -> None:
        del path, environ
        self.is_ready = True

    async def shutdown(self) -> None:
        self.closed += 1
        self.is_ready = False


class IdleDeliveryLoop:
    def __init__(
        self,
        worker: object,
        *,
        poll_seconds: float,
        delivery_interval_seconds: float,
    ) -> None:
        del worker, poll_seconds, delivery_interval_seconds

    async def run(self) -> None:
        await asyncio.Event().wait()


class Callbacks:
    def __init__(self, **kwargs: object) -> None:
        del kwargs

    async def execute(self, update: object) -> bool:
        del update
        return False

    async def synchronize_pending_once(
        self, *, owner: str, lease_seconds: float
    ) -> bool:
        del owner, lease_seconds
        return False


class Poller:
    def __init__(self) -> None:
        self.started = 0
        self.stopped = 0

    async def start_polling(self, bot: object, **kwargs: object) -> None:
        del bot, kwargs
        self.started += 1

    async def stop_polling(self) -> None:
        self.stopped += 1

    def include_router(self, router: object) -> None:
        self.router = router


class CapturingRouter:
    instance: CapturingRouter | None = None

    def __init__(self, *, name: str) -> None:
        del name
        self.start_handler: Any = None
        self.callback_handler: Any = None
        CapturingRouter.instance = self

    def message(self, *filters: object) -> Callable[[object], object]:
        del filters

        def decorate(function: object) -> object:
            self.start_handler = function
            return function

        return decorate

    def callback_query(self) -> Callable[[object], object]:
        def decorate(function: object) -> object:
            self.callback_handler = function
            return function

        return decorate


def test_approval_bot_start_and_shutdown_own_resources_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        foundation = Foundation()
        gateway = Gateway()
        repository = MemoryRepository()
        admin = Administrator(
            7,
            True,
            "admin",
            frozenset({AdminPermission.VIEW, AdminPermission.TOGGLE}),
            frozenset({-1001}),
        )

        async def components(*args: object, **kwargs: object) -> object:
            del args, kwargs
            return SimpleNamespace(
                gateway=gateway,
                repository=repository,
                authorize=AuthorizeAdminAction((admin,)),
            )

        async def initialize(*args: object) -> None:
            del args

        monkeypatch.setattr(module, "create_admin_approval_components", components)
        monkeypatch.setattr(
            module, "initialize_operational_approval_indexes", initialize
        )
        monkeypatch.setattr(module, "initialize_publication_indexes", initialize)
        monkeypatch.setattr(module, "initialize_native_schedule_indexes", initialize)
        monkeypatch.setattr(module, "ApprovalDeliveryLoop", IdleDeliveryLoop)
        monkeypatch.setattr(module, "ApprovalCallbackExecutor", Callbacks)
        monkeypatch.setattr(module, "Router", CapturingRouter)
        monkeypatch.setattr(module, "Dispatcher", Poller)
        application = module.ApprovalBotApplication(cast("Any", foundation))
        await application.start(cast("Any", "config.json"), environ={})
        assert "approval_bot_started" in foundation.logger.events
        await asyncio.sleep(0)
        await application.wait()
        poller = cast("Poller", application._dispatcher)
        assert poller.started == 1
        router = CapturingRouter.instance
        assert router is not None
        message = SimpleNamespace(
            from_user=SimpleNamespace(id=7),
            chat=SimpleNamespace(id=7, type="private"),
        )
        await router.start_handler(message)
        query = SimpleNamespace(
            id="query",
            message=None,
            from_user=SimpleNamespace(id=7),
            data="invalid",
        )
        await router.callback_handler(query)
        query.message = message
        await router.callback_handler(query)
        await application.shutdown()
        await application.shutdown()
        assert gateway.closed == 1
        assert foundation.closed == 1
        assert "approval_bot_stopped" in foundation.logger.events

    asyncio.run(scenario())


def test_approval_bot_run_boundary_maps_success_and_failure() -> None:
    class Application:
        def __init__(self, *, fail: bool) -> None:
            self.fail = fail
            self.closed = 0

        async def start(self, path: object, *, environ: object) -> None:
            del path, environ
            if self.fail:
                raise module.ApprovalBotStartupError

        async def wait(self) -> None:
            return None

        async def shutdown(self) -> None:
            self.closed += 1

    async def scenario() -> None:
        successful = Application(fail=False)
        result = await module.run_approval_bot_application(
            cast("Any", successful), cast("Any", "config"), environ={}
        )
        assert result is FoundationExitCode.SUCCESS
        assert successful.closed == 1
        failed = Application(fail=True)
        result = await module.run_approval_bot_application(
            cast("Any", failed), cast("Any", "config"), environ={}
        )
        assert result is FoundationExitCode.INFRASTRUCTURE_ERROR
        assert failed.closed == 1
        configuration_failure = Application(fail=False)

        async def fail_with_configuration(path: object, *, environ: object) -> None:
            del path, environ
            error = module.ApprovalBotStartupError()
            error.__cause__ = FoundationConfigurationError()
            raise error

        configuration_failure.start = fail_with_configuration  # type: ignore[method-assign]
        result = await module.run_approval_bot_application(
            cast("Any", configuration_failure), cast("Any", "config"), environ={}
        )
        assert result is FoundationExitCode.CONFIGURATION_ERROR
        assert isinstance(
            module.create_approval_bot_application(sink=cast("Any", object())),
            module.ApprovalBotApplication,
        )

    asyncio.run(scenario())


def test_approval_bot_rejects_wait_before_start_and_cleans_start_failures() -> None:
    class FailingFoundation(Foundation):
        def __init__(self, error: BaseException) -> None:
            super().__init__()
            self.error = error

        async def start(self, path: object, *, environ: object) -> None:
            del path, environ
            raise self.error

    async def scenario() -> None:
        fresh = module.ApprovalBotApplication(cast("Any", Foundation()))
        with pytest.raises(module.ApprovalBotStartupError):
            await fresh.wait()

        failed = module.ApprovalBotApplication(
            cast("Any", FailingFoundation(ValueError("safe")))
        )
        with pytest.raises(module.ApprovalBotStartupError):
            await failed.start(cast("Any", "config"), environ={})

        cancelled = module.ApprovalBotApplication(
            cast("Any", FailingFoundation(asyncio.CancelledError()))
        )
        with pytest.raises(asyncio.CancelledError):
            await cancelled.start(cast("Any", "config"), environ={})

        class CancelledApplication:
            async def start(self, path: object, *, environ: object) -> None:
                del path, environ

            async def wait(self) -> None:
                raise asyncio.CancelledError

            async def shutdown(self) -> None:
                return None

        with pytest.raises(asyncio.CancelledError):
            await module.run_approval_bot_application(
                cast("Any", CancelledApplication()),
                cast("Any", "config"),
                environ={},
            )

    asyncio.run(scenario())


def test_approval_background_supervisor_uses_safe_structured_logging() -> None:
    class BlockingPoller(Poller):
        async def start_polling(self, bot: object, **kwargs: object) -> None:
            del bot, kwargs
            self.started += 1
            await asyncio.Event().wait()

    async def scenario() -> None:
        events: list[dict[str, object]] = []

        def capture(event: StructuredEvent) -> None:
            events.append(dict(event))

        logger = StructuredLogger(
            sink=capture,
            clock=lambda: datetime(2026, 7, 13, tzinfo=UTC),
            redactor=Redactor(secret_values=()),
            minimum_level=LogLevel.DEBUG,
        )

        async def run_worker(
            worker: asyncio.Task[None],
        ) -> tuple[BaseException | None, dict[str, object]]:
            async def block() -> None:
                await asyncio.Event().wait()

            foundation = Foundation()
            foundation.logger = cast("Any", logger)
            application = module.ApprovalBotApplication(cast("Any", foundation))
            application._started = True
            application._dispatcher = cast("Any", BlockingPoller())
            application._gateway = cast("Any", Gateway())
            application._delivery_task = worker
            sync_task = asyncio.create_task(block(), name="approval-sync")
            application._sync_task = sync_task
            try:
                with pytest.raises(module.ApprovalBotStartupError) as captured:
                    await application.wait()
            finally:
                sync_task.cancel()
                await asyncio.gather(sync_task, return_exceptions=True)
            return captured.value.__cause__, events[-1]

        original = RuntimeError("private worker detail must not be logged")

        async def fail() -> None:
            raise original

        cause, event = await run_worker(
            asyncio.create_task(fail(), name="approval-delivery")
        )
        assert cause is original
        assert event["event_name"] == "approval_background_task_failed"
        assert event["task_name"] == "approval-delivery"
        assert event["failure_category"] == "transient"
        assert event["failure_type"] == "RuntimeError"
        assert "error_category" not in event
        assert "error_message" not in event
        assert "private worker detail" not in str(event)

        async def finish() -> None:
            return None

        cause, event = await run_worker(
            asyncio.create_task(finish(), name="approval-sync")
        )
        assert isinstance(cause, module.ApprovalBackgroundTaskStoppedError)
        assert event["task_name"] == "approval-sync"
        assert event["failure_type"] == "ApprovalBackgroundTaskStoppedError"

        async def block() -> None:
            await asyncio.Event().wait()

        cancelled = asyncio.create_task(block(), name="approval-delivery")
        cancelled.cancel()
        cause, event = await run_worker(cancelled)
        assert isinstance(cause, asyncio.CancelledError)
        assert event["failure_type"] == "CancelledError"

    asyncio.run(scenario())


def test_approval_shutdown_drains_polling_before_closing_bot_session() -> None:
    async def scenario() -> None:
        order: list[str] = []
        entered = asyncio.Event()
        loop_errors: list[dict[str, object]] = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))

        class DrainingPoller(Poller):
            async def start_polling(self, bot: object, **kwargs: object) -> None:
                del bot, kwargs
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    await asyncio.sleep(0)
                    order.append("polling-finished")

            async def stop_polling(self) -> None:
                order.append("stop-requested")

        class OrderedGateway(Gateway):
            async def close(self) -> None:
                assert "polling-finished" in order
                order.append("bot-session-closed")
                await super().close()

        async def block() -> None:
            await asyncio.Event().wait()

        foundation = Foundation()
        application = module.ApprovalBotApplication(cast("Any", foundation))
        application._started = True
        application._dispatcher = cast("Any", DrainingPoller())
        gateway = OrderedGateway()
        application._gateway = cast("Any", gateway)
        application._delivery_task = asyncio.create_task(
            block(), name="approval-delivery"
        )
        application._sync_task = asyncio.create_task(block(), name="approval-sync")
        wait_task = asyncio.create_task(application.wait(), name="approval-wait")
        try:
            await entered.wait()
            await application.shutdown()
            await asyncio.gather(wait_task, return_exceptions=True)
            await asyncio.sleep(0)
        finally:
            loop.set_exception_handler(previous_handler)

        assert order == [
            "stop-requested",
            "polling-finished",
            "bot-session-closed",
        ]
        assert gateway.closed == 1
        assert not loop_errors

    asyncio.run(scenario())
