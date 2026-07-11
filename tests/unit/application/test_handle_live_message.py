from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import (
    HandleLiveMessage,
    IngestionOutcome,
    IngestionResult,
    LiveMessageOutcome,
)
from telegram_assist_bot.application.ports import TelegramTextMessage
from telegram_assist_bot.domain.posts import PostId

if TYPE_CHECKING:
    from collections.abc import Coroutine


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass
class Ingestor:
    results: list[IngestionResult]
    calls: list[tuple[int, str]] = field(default_factory=list)

    async def execute(
        self,
        item: TelegramTextMessage,
        *,
        correlation_id: str,
    ) -> IngestionResult:
        self.calls.append((item.source_message_id, correlation_id))
        return self.results.pop(0)


@pytest.fixture
def text_message() -> TelegramTextMessage:
    return TelegramTextMessage(
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=7,
        text="سلام‌دنیا\n😀",
        caption=None,
        text_entities=(),
        caption_entities=(),
        source_published_at=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        is_service=False,
        has_media=False,
    )


def test_maps_created_and_duplicate_shared_ingestion_results(
    text_message: TelegramTextMessage,
) -> None:
    ingestor = Ingestor(
        [
            IngestionResult(IngestionOutcome.CREATED, PostId("canonical"), True),
            IngestionResult(
                IngestionOutcome.ALREADY_EXISTS,
                PostId("canonical"),
                False,
            ),
        ]
    )
    use_case = HandleLiveMessage(ingestor)

    first = run(
        use_case.execute(
            text_message,
            source_channel_id=-1001,
            correlation_id="listener-1",
        )
    )
    second = run(
        use_case.execute(
            text_message,
            source_channel_id=-1001,
            correlation_id="listener-1",
        )
    )

    assert first is LiveMessageOutcome.CREATED
    assert second is LiveMessageOutcome.ALREADY_EXISTS
    assert ingestor.calls == [(7, "listener-1"), (7, "listener-1")]


def test_rejects_other_source_before_shared_ingestion(
    text_message: TelegramTextMessage,
) -> None:
    ingestor = Ingestor([])

    outcome = run(
        HandleLiveMessage(ingestor).execute(
            text_message,
            source_channel_id=-2002,
            correlation_id="listener-1",
        )
    )

    assert outcome is LiveMessageOutcome.SKIPPED_OTHER_SOURCE
    assert ingestor.calls == []


def test_rejects_service_event(text_message: TelegramTextMessage) -> None:
    ingestor = Ingestor([])
    service = replace(text_message, is_service=True)

    outcome = run(
        HandleLiveMessage(ingestor).execute(
            service,
            source_channel_id=-1001,
            correlation_id="listener-1",
        )
    )

    assert outcome is LiveMessageOutcome.SKIPPED_SERVICE


def test_rejects_media_only_event(text_message: TelegramTextMessage) -> None:
    ingestor = Ingestor([])
    media_only = replace(
        text_message,
        text=None,
        text_entities=(),
        has_media=True,
    )

    outcome = run(
        HandleLiveMessage(ingestor).execute(
            media_only,
            source_channel_id=-1001,
            correlation_id="listener-1",
        )
    )

    assert outcome is LiveMessageOutcome.SKIPPED_MEDIA_ONLY
