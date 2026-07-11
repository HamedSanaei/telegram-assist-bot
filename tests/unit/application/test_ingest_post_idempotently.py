from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application import (
    IngestionOutcome,
    IngestPostIdempotently,
    build_stored_post,
)
from telegram_assist_bot.application.ports import (
    InsertPostOutcome,
    InsertPostResult,
    PostClaimOutcome,
    PostClaimRequest,
    PostClaimResult,
    PostRepositoryUnavailableError,
    TelegramTextMessage,
)
from telegram_assist_bot.domain.posts import Post, PostId, SourceMessageIdentity

if TYPE_CHECKING:
    from collections.abc import Coroutine, Mapping

    from telegram_assist_bot.application.ports import PostTransitionRequest
    from telegram_assist_bot.shared.config import LogLevel


def run[T](coroutine: Coroutine[object, object, T]) -> T:
    return asyncio.run(coroutine)


@dataclass(frozen=True)
class Clock:
    def utc_now(self) -> datetime:
        return datetime(2099, 3, 20, 8, 0, tzinfo=UTC)


@dataclass
class AtomicRepository:
    posts: dict[SourceMessageIdentity, Post] = field(default_factory=dict)
    claims: set[PostId] = field(default_factory=set)
    unavailable: bool = False

    async def insert_idempotently(self, post: Post) -> InsertPostResult:
        if self.unavailable:
            raise PostRepositoryUnavailableError
        existing = self.posts.get(post.source_identity)
        if existing is None:
            self.posts[post.source_identity] = post
            return InsertPostResult(InsertPostOutcome.CREATED, post.post_id)
        same = (
            existing.original_content == post.original_content
            and existing.source_published_at == post.source_published_at
        )
        return InsertPostResult(
            InsertPostOutcome.ALREADY_EXISTS if same else InsertPostOutcome.CONFLICT,
            existing.post_id,
        )

    async def claim_for_next_stage(self, request: PostClaimRequest) -> PostClaimResult:
        if request.post_id in self.claims:
            return PostClaimResult(PostClaimOutcome.ALREADY_CLAIMED, request.post_id)
        self.claims.add(request.post_id)
        return PostClaimResult(PostClaimOutcome.CLAIMED, request.post_id)

    async def get_by_id(self, post_id: PostId, *, as_of: datetime) -> Post | None:
        del as_of
        return next(
            (post for post in self.posts.values() if post.post_id == post_id), None
        )

    async def get_by_source_identity(
        self,
        source_identity: SourceMessageIdentity,
        *,
        as_of: datetime,
    ) -> Post | None:
        del as_of
        return self.posts.get(source_identity)

    async def list_unexpired(self, *, as_of: datetime, limit: int) -> tuple[Post, ...]:
        del as_of
        return tuple(self.posts.values())[:limit]

    async def transition(self, request: PostTransitionRequest) -> Post:
        return request.post


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


def message(*, text: str = "متن‌اصلی\n😀") -> TelegramTextMessage:
    return TelegramTextMessage(
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع آزمایشی",
        source_message_id=7,
        text=text,
        caption=None,
        text_entities=(),
        caption_entities=(),
        source_published_at=datetime(2099, 3, 20, 7, 59, tzinfo=UTC),
        is_service=False,
        has_media=False,
    )


def ingestor(repository: AtomicRepository, post_id: str) -> IngestPostIdempotently:
    return IngestPostIdempotently(
        repository,
        Clock(),
        lambda _identity: PostId(post_id),
        Logger(),
    )


def test_created_call_wins_exactly_one_downstream_claim() -> None:
    repository = AtomicRepository()

    first = run(
        ingestor(repository, "candidate-a").execute(message(), correlation_id="c1")
    )
    second = run(
        ingestor(repository, "candidate-b").execute(message(), correlation_id="c2")
    )

    assert first.outcome is IngestionOutcome.CREATED
    assert first.downstream_claimed is True
    assert second.outcome is IngestionOutcome.ALREADY_EXISTS
    assert second.downstream_claimed is False
    assert first.post_id == second.post_id == PostId("candidate-a")
    assert len(repository.posts) == 1
    assert len(repository.claims) == 1


def test_conflicting_source_payload_is_not_overwritten_or_claimed() -> None:
    repository = AtomicRepository()
    run(ingestor(repository, "candidate-a").execute(message(), correlation_id="c1"))

    conflict = run(
        ingestor(repository, "candidate-b").execute(
            message(text="متن متفاوت"),
            correlation_id="c2",
        )
    )

    assert conflict.outcome is IngestionOutcome.CONFLICT
    assert conflict.downstream_claimed is False
    assert next(iter(repository.posts.values())).original_text == "متن‌اصلی\n😀"


def test_retry_after_insert_before_claim_recovers_without_duplicate() -> None:
    repository = AtomicRepository()
    candidate = ingestor(repository, "canonical")
    stored = build_stored_post(
        message(),
        received_at=Clock().utc_now(),
        post_id_factory=lambda _identity: PostId("canonical"),
    )
    inserted = run(repository.insert_idempotently(stored))

    recovered = run(candidate.execute(message(), correlation_id="same-correlation"))

    assert inserted.outcome is InsertPostOutcome.CREATED
    assert recovered.outcome is IngestionOutcome.ALREADY_EXISTS
    assert recovered.downstream_claimed is True
    assert len(repository.posts) == 1


def test_unrelated_infrastructure_failure_propagates() -> None:
    repository = AtomicRepository(unavailable=True)

    with pytest.raises(PostRepositoryUnavailableError):
        run(ingestor(repository, "candidate").execute(message(), correlation_id="c1"))
