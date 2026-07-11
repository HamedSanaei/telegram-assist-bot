from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from tests.integration.test_crawl_today_text_posts import (
    MongoTestSettings,
    Resources,
    resources,
)

from telegram_assist_bot.application import (
    IngestionOutcome,
    IngestionResult,
    IngestPostIdempotently,
    build_stored_post,
)
from telegram_assist_bot.application.ports import TelegramTextMessage
from telegram_assist_bot.domain.posts import PostId

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from telegram_assist_bot.shared.config import LogLevel

pytestmark = pytest.mark.integration

_NOW = datetime(2099, 3, 20, 8, 0, tzinfo=UTC)


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass(frozen=True)
class Clock:
    def utc_now(self) -> datetime:
        return _NOW


@dataclass
class Logger:
    events: list[tuple[str, Mapping[str, object] | None]] = field(default_factory=list)

    def emit(
        self,
        *,
        level: LogLevel,
        event_name: str,
        fields: Mapping[str, object] | None = None,
        error: BaseException | None = None,
    ) -> None:
        del level, error
        self.events.append((event_name, fields))


def message(message_id: int = 7, *, text: str = "متن‌هم‌زمان\n😀") -> TelegramTextMessage:
    return TelegramTextMessage(
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=message_id,
        text=text,
        caption=None,
        text_entities=(),
        caption_entities=(),
        source_published_at=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        is_service=False,
        has_media=False,
    )


def ingestor(owned: Resources, candidate_id: str) -> IngestPostIdempotently:
    return IngestPostIdempotently(
        owned.repository.__class__(owned.collection, 5),
        Clock(),
        lambda _identity: PostId(candidate_id),
        Logger(),
    )


def release_together(count: int) -> tuple[asyncio.Event, list[asyncio.Event]]:
    """Create one deterministic start gate and per-producer ready signals."""
    gate = asyncio.Event()
    ready = [asyncio.Event() for _ in range(count)]
    return gate, ready


def test_concurrent_crawl_listener_and_workers_share_one_post_and_claim(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            producer_count = 20
            gate, ready = release_together(producer_count)

            async def producer(index: int) -> IngestionResult:
                ready[index].set()
                await gate.wait()
                return await ingestor(owned, f"candidate-{index}").execute(
                    message(),
                    correlation_id=f"producer-{index}",
                )

            tasks = [
                asyncio.create_task(producer(index)) for index in range(producer_count)
            ]
            await asyncio.gather(*(signal.wait() for signal in ready))
            gate.set()
            results = await asyncio.gather(*tasks)

            canonical_ids = {result.post_id for result in results}
            assert await owned.collection.count_documents({}) == 1
            assert len(canonical_ids) == 1
            assert sum(result.downstream_claimed for result in results) == 1
            assert (
                sum(result.outcome is IngestionOutcome.CREATED for result in results)
                == 1
            )
            document = await owned.collection.find_one({})
            assert document is not None
            assert document["next_stage_claimed_at"] is not None

    run(scenario())


def test_crash_after_insert_before_claim_recovers_without_duplicate(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            candidate = build_stored_post(
                message(),
                received_at=_NOW,
                post_id_factory=lambda _identity: PostId("canonical-before-crash"),
            )
            inserted = await owned.repository.insert_idempotently(candidate)
            assert inserted.outcome.value == "Created"

            recovered = await ingestor(owned, "retry-candidate").execute(
                message(),
                correlation_id="recovery",
            )

            assert recovered.outcome is IngestionOutcome.ALREADY_EXISTS
            assert recovered.post_id == PostId("canonical-before-crash")
            assert recovered.downstream_claimed is True
            assert await owned.collection.count_documents({}) == 1

    run(scenario())


def test_different_identities_do_not_share_canonical_ids_or_claims(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            results = await asyncio.gather(
                *(
                    ingestor(owned, f"canonical-{index}").execute(
                        message(index + 1),
                        correlation_id=f"different-{index}",
                    )
                    for index in range(12)
                )
            )

            assert await owned.collection.count_documents({}) == 12
            assert len({result.post_id for result in results}) == 12
            assert all(result.downstream_claimed for result in results)

    run(scenario())


def test_conflicting_duplicate_is_distinct_and_does_not_overwrite(
    mongodb_test_settings: MongoTestSettings,
) -> None:
    async def scenario() -> None:
        async with resources(mongodb_test_settings) as owned:
            first = await ingestor(owned, "canonical").execute(
                message(),
                correlation_id="first",
            )
            conflict = await ingestor(owned, "other").execute(
                message(text="محتوای متفاوت"),
                correlation_id="conflict",
            )

            assert first.outcome is IngestionOutcome.CREATED
            assert conflict.outcome is IngestionOutcome.CONFLICT
            assert conflict.downstream_claimed is False
            stored = await owned.repository.get_by_id(first.post_id, as_of=_NOW)
            assert stored is not None
            assert stored.original_text == "متن‌هم‌زمان\n😀"

    run(scenario())
