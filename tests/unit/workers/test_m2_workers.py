"""Verify Milestone 2 workers remain thin application triggers."""

import asyncio
from datetime import UTC, datetime

from telegram_assist_bot.workers.content_preparation import prepare_content_once
from telegram_assist_bot.workers.media_cleanup import cleanup_media_once
from telegram_assist_bot.workers.media_group_assembler import finalize_media_group_once


class Callable:
    async def execute(self, *args: object, **kwargs: object) -> object:
        return args[0] if args else kwargs.get("now")

    async def finalize_if_due(self, group_key: str, *, now: datetime) -> bool:
        del now
        return group_key == "group"


def test_worker_delegation() -> None:
    async def scenario() -> None:
        value = object()
        assert await prepare_content_once(Callable(), value) is value  # type: ignore[arg-type]
        now = datetime(2026, 1, 1, tzinfo=UTC)
        assert await cleanup_media_once(Callable(), now=now) == now  # type: ignore[arg-type]
        assert await finalize_media_group_once(Callable(), "group", now=now)  # type: ignore[arg-type]

    asyncio.run(scenario())
