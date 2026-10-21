"""Preserve immutable original snapshots without blocking later history records."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ingest_post_idempotently import (
    IngestionOutcome,
    IngestionResult,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import (
    PostId,
    SourceMessageIdentity,
    TelegramEntity,
    TelegramUrlButton,
)
from tests.unit.application.test_crawl_today_text_posts import (
    FakeRepository,
    crawler,
    run,
)
from tests.unit.application.test_history_recovery import Checkpoints, gateway
from tests.unit.application.test_runtime_ingestion import Clock, coordinator, message

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from telegram_assist_bot.application.ports import TelegramTextMessage


@pytest.mark.parametrize(
    "attribute",
    [
        "source_channel_username",
        "source_channel_display_name",
        "source_published_at",
        "text",
        "caption",
        "text_entities",
        "caption_entities",
        "inline_keyboard",
    ],
)
def test_completed_source_edit_is_a_conflict_without_mutation_or_media_replay(
    tmp_path: Path,
    attribute: str,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        runtime, repository, source, _ = coordinator(tmp_path, Clock(now))
        original = message(
            1,
            now,
            media_type=None
            if attribute in {"text", "text_entities"}
            else MediaType.PHOTO,
            caption="متن‌اصلی 😀",
        )
        first = await runtime.execute(original, correlation_id="original")
        opens = source.opens
        mutations: dict[str, Callable[[TelegramTextMessage], TelegramTextMessage]] = {
            "source_channel_username": lambda item: replace(
                item, source_channel_username="renamed_source"
            ),
            "source_channel_display_name": lambda item: replace(
                item, source_channel_display_name="نام تازه"
            ),
            "source_published_at": lambda item: replace(
                item, source_published_at=now - timedelta(seconds=1)
            ),
            "text": lambda item: replace(item, text="ویرایش جدید"),
            "caption": lambda item: replace(item, caption="ویرایش جدید"),
            "text_entities": lambda item: replace(
                item, text_entities=(TelegramEntity(0, 1, "bold"),)
            ),
            "caption_entities": lambda item: replace(
                item, caption_entities=(TelegramEntity(0, 1, "bold"),)
            ),
            "inline_keyboard": lambda item: replace(
                item,
                inline_keyboard=(
                    (TelegramUrlButton("پیوند", "https://example.invalid"),),
                ),
            ),
        }
        edited = mutations[attribute](original)
        conflict = await runtime.execute(edited, correlation_id="edited")
        assert conflict.outcome is IngestionOutcome.CONFLICT
        assert conflict.post_id == first.post_id
        assert not conflict.downstream_claimed
        stored = await runtime.post_repository.get_by_source_identity(
            SourceMessageIdentity(-100, 1),
            as_of=now,
        )
        assert stored is not None
        assert stored.original_content.caption == original.caption
        assert stored.original_content.text == original.text
        assert stored.original_content.text_entities == original.text_entities
        assert stored.original_content.caption_entities == original.caption_entities
        assert stored.original_content.inline_keyboard == original.inline_keyboard
        assert stored.source_channel_username == original.source_channel_username
        assert (
            stored.source_channel_display_name == original.source_channel_display_name
        )
        assert stored.source_published_at == original.source_published_at
        assert source.opens == opens
        await runtime.execute(message(2, now), correlation_id="next-message")
        assert len(repository.ready) == 2

    asyncio.run(scenario())


def test_immutable_conflict_is_acknowledged_without_starving_later_history() -> None:
    now = datetime(2026, 7, 11, 10, tzinfo=UTC)
    source, store, posts = gateway(4), Checkpoints(), FakeRepository()
    base = crawler(source, posts, now)

    class ConflictingIngestor:
        async def execute(
            self,
            item: TelegramTextMessage,
            *,
            correlation_id: str,
        ) -> IngestionResult:
            if item.source_message_id == 4:
                return IngestionResult(
                    IngestionOutcome.CONFLICT, PostId("original-4"), False
                )
            return await base.ingestor.execute(item, correlation_id=correlation_id)

    result = run(
        replace(base, checkpoints=store, ingestor=ConflictingIngestor()).execute(
            -1001,
            correlation_id="immutable-conflict",
        )
    )
    assert result.completed
    assert result.failed == 1
    assert {identity.source_message_id for identity in posts.posts} == {1, 2, 3}
    assert store.failures == {4: "concurrency_conflict"}
