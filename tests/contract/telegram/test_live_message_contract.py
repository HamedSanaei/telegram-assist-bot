from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.infrastructure.telegram.user import TelethonLiveAdapter

if TYPE_CHECKING:
    from collections.abc import Coroutine

pytestmark = pytest.mark.contract


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class RecordedEventClient:
    callback: object | None = None
    builder: object | None = None
    removed: int = 0

    def add_event_handler(self, callback: object, event: object) -> None:
        self.callback = callback
        self.builder = event

    def remove_event_handler(self, callback: object, event: object) -> int:
        assert callback is self.callback
        assert event is self.builder
        self.removed += 1
        return 1


def test_live_sdk_event_maps_to_shared_text_dto_and_unsubscribes() -> None:
    async def scenario() -> None:
        client = RecordedEventClient()
        subscription = await TelethonLiveAdapter(
            client,
            "source_fixture",
            "منبع آزمایشی",
        ).subscribe(-1001, buffer_size=2)
        callback = client.callback
        assert callable(callback)
        original = "سلام‌زنده\n😀"
        raw = SimpleNamespace(
            id=11,
            date=datetime(2099, 3, 20, 8, 0, tzinfo=UTC),
            message=original,
            entities=[],
            media=None,
            action=None,
        )

        await callback(SimpleNamespace(message=raw))
        mapped = await anext(subscription)
        await subscription.close()

        assert mapped.source_channel_id == -1001
        assert mapped.source_message_id == 11
        assert mapped.text == original
        assert mapped.caption is None
        assert client.removed == 1

    run(scenario())
