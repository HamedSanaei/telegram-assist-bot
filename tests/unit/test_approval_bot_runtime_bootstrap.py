"""Non-live lifecycle tests for the operational approval Bot composition."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest

import telegram_assist_bot.bootstrap.approval_bot as module
from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.application.ports import BotEditOutcome
from telegram_assist_bot.bootstrap.runtime import (
    FoundationConfigurationError,
    FoundationExitCode,
)
from telegram_assist_bot.domain import Administrator, AdminPermission
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
        self, chat_id: int, text: str, keyboard: object = None
    ) -> int:
        del chat_id, text, keyboard
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
    def __init__(self, worker: object, *, poll_seconds: float) -> None:
        del worker, poll_seconds

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
