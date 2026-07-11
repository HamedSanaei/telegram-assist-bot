from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pytest
from tests.e2e.test_text_ingestion_restart import (
    Sink,
    create_application,
    environment,
    write_configuration,
)
from tests.integration.test_crawl_today_text_posts import MongoTestSettings, resources
from tests.unit.test_text_ingestion_bootstrap import Gateway, source_message

from telegram_assist_bot.application.ports import (
    TelegramAccount,
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramSessionInvalidError,
)
from telegram_assist_bot.bootstrap.text_ingestion import TextIngestionStartupError
from telegram_assist_bot.shared.errors import TransientOperationError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Coroutine
    from pathlib import Path

pytestmark = pytest.mark.integration


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class RecoveringGateway(Gateway):
    history_attempts: int = 0

    async def iter_history_pages(
        self,
        query: TelegramHistoryQuery,
    ) -> AsyncIterator[TelegramHistoryPage]:
        del query
        self.history_attempts += 1
        if self.history_attempts == 1:
            raise TransientOperationError
        yield TelegramHistoryPage(self.history)


@dataclass
class InvalidSessionGateway(Gateway):
    async def validate_account(self) -> TelegramAccount:
        raise TelegramSessionInvalidError


def test_transient_history_failure_recovers_without_session_or_task_leak(
    tmp_path: Path,
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        session_path = tmp_path / "synthetic.session"
        session_value = "synthetic-session-v1"
        session_path.write_text(session_value, encoding="utf-8")
        config_path = write_configuration(tmp_path, mongodb_test_settings, session_path)
        gateway = RecoveringGateway(
            history=(source_message(1),),
            live=[source_message(1), source_message(2)],
            order=[],
        )
        baseline = set(asyncio.all_tasks())
        app = create_application(gateway, Sink())

        await app.start(config_path, environ=environment(mongodb_test_settings))
        await app.wait()
        await app.shutdown()
        await asyncio.sleep(0)

        async with resources(mongodb_test_settings) as owned:
            assert await owned.collection.count_documents({}) == 2
        assert gateway.history_attempts == 2
        assert session_path.read_text(encoding="utf-8") == session_value
        assert not [
            task
            for task in asyncio.all_tasks()
            if task not in baseline and not task.done()
        ]

    run(scenario())


def test_invalid_session_fails_before_subscription_and_preserves_session(
    tmp_path: Path,
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        session_path = tmp_path / "synthetic.session"
        session_value = "synthetic-session-v1"
        session_path.write_text(session_value, encoding="utf-8")
        config_path = write_configuration(tmp_path, mongodb_test_settings, session_path)
        gateway = InvalidSessionGateway((), [], [])
        app = create_application(gateway, Sink())

        with pytest.raises(TextIngestionStartupError):
            await app.start(config_path, environ=environment(mongodb_test_settings))

        assert gateway.subscription is None
        assert gateway.close_calls == 1
        assert session_path.read_text(encoding="utf-8") == session_value
        async with resources(mongodb_test_settings) as owned:
            assert await owned.collection.count_documents({}) == 0

    run(scenario())
