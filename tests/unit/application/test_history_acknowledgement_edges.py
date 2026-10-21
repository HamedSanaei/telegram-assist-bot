"""Reject unsafe cursor advancement while retaining all individually persisted work."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from telegram_assist_bot.application.crawl_today_text_posts import (
    HistoryPaginationLimitError,
)
from telegram_assist_bot.application.ports import (
    TelegramHistoryPage,
    TelegramMessageMappingError,
)
from telegram_assist_bot.application.ports.history_checkpoint import HistoryCheckpoint
from tests.unit.application.test_crawl_today_text_posts import (
    FakeHistoryGateway,
    FakeRepository,
    crawler,
    message,
    run,
)
from tests.unit.application.test_history_recovery import Checkpoints

NOW = datetime(2026, 7, 11, 10, tzinfo=UTC)


def test_unidentified_failure_preserves_cursor_and_ingests_valid_member() -> None:
    store, repository = Checkpoints(), FakeRepository()
    source = FakeHistoryGateway(
        (
            TelegramHistoryPage(
                (message(4, NOW - timedelta(hours=1)),),
                4,
                True,
                (TelegramMessageMappingError(None),),
            ),
        )
    )
    result = run(
        replace(crawler(source, repository, NOW), checkpoints=store).execute(
            -1001,
            correlation_id="unknown-source-id",
        )
    )
    assert result.created == 1
    assert result.failed == 1
    assert not result.completed
    assert store.state is not None
    assert store.state.offset_message_id == 0
    assert not store.failures
    assert {identity.source_message_id for identity in repository.posts} == {4}


@pytest.mark.parametrize("cursor", [0, 5])
def test_non_advancing_cursor_never_acknowledges_a_persisted_page(cursor: int) -> None:
    checkpoint = HistoryCheckpoint(
        -1001,
        NOW.replace(hour=0),
        NOW,
        offset_message_id=5,
    )
    store, repository = Checkpoints(checkpoint), FakeRepository()
    source = FakeHistoryGateway(
        (
            TelegramHistoryPage(
                (message(4, NOW - timedelta(hours=1)),),
                cursor,
                True,
            ),
        )
    )
    with pytest.raises(HistoryPaginationLimitError):
        run(
            replace(crawler(source, repository, NOW), checkpoints=store).execute(
                -1001,
                correlation_id="invalid-cursor",
            )
        )
    assert store.state == checkpoint
    assert {identity.source_message_id for identity in repository.posts} == {4}


def test_lost_initial_checkpoint_cas_does_not_start_an_unowned_scan() -> None:
    class LostClaim(Checkpoints):
        async def save(
            self,
            checkpoint: HistoryCheckpoint,
            *,
            expected_revision: int | None,
        ) -> bool:
            return False

    repository = FakeRepository()
    source = FakeHistoryGateway((TelegramHistoryPage((message(1, NOW),)),))
    result = run(
        replace(crawler(source, repository, NOW), checkpoints=LostClaim()).execute(
            -1001,
            correlation_id="lost-source-cas",
        )
    )
    assert not result.completed
    assert not repository.posts
    assert not source.queries
