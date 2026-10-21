from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ports import (
    TelegramHistoryPage,
    TelegramHistoryQuery,
    TelegramRateLimitError,
)
from telegram_assist_bot.application.ports.history_checkpoint import HistoryCheckpoint
from telegram_assist_bot.shared.errors import (
    PermanentOperationError,
    TransientOperationError,
)
from telegram_assist_bot.shared.retry import RetryPolicy
from tests.unit.application.test_crawl_today_text_posts import (
    FakeHistoryGateway,
    FakeRepository,
    RecordingLogger,
    crawler,
    message,
    run,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from telegram_assist_bot.application.ingest_post_idempotently import IngestionResult
    from telegram_assist_bot.application.ports import TelegramTextMessage

NOW = datetime(2026, 7, 11, 10, tzinfo=UTC)


@dataclass
class Checkpoints:
    state: HistoryCheckpoint | None = None
    failures: dict[int, str] = field(default_factory=dict)

    async def get(self, source_channel_id: int) -> HistoryCheckpoint | None:
        del source_channel_id
        return self.state

    async def save(
        self, checkpoint: HistoryCheckpoint, *, expected_revision: int | None
    ) -> bool:
        revision = self.state.revision if self.state else None
        if revision != expected_revision:
            return False
        self.state = checkpoint
        return True

    async def is_terminal_failure(
        self, source_channel_id: int, source_message_id: int
    ) -> bool:
        del source_channel_id
        return source_message_id in self.failures

    async def record_terminal_failure(
        self,
        source_channel_id: int,
        source_message_id: int,
        *,
        category: str,
        recorded_at: datetime,
    ) -> None:
        del source_channel_id, recorded_at
        self.failures[source_message_id] = category


@dataclass
class ResumableGateway(FakeHistoryGateway):
    items: tuple[TelegramTextMessage, ...] = ()
    fail_at: dict[int, list[Exception]] = field(default_factory=dict)
    fetched: list[int] = field(default_factory=list)

    async def iter_history_pages(
        self, query: object
    ) -> AsyncIterator[TelegramHistoryPage]:
        assert isinstance(query, TelegramHistoryQuery)
        self.queries.append(query)
        selected = [
            item
            for item in self.items
            if (
                not query.offset_message_id
                or item.source_message_id < query.offset_message_id
            )
            and item.source_published_at < query.end_exclusive
        ]
        offset = query.offset_message_id
        for index in range(0, len(selected), query.page_size):
            self.fetched.append(offset)
            failures = self.fail_at.get(offset, [])
            if failures:
                raise failures.pop(0)
            batch = tuple(selected[index : index + query.page_size])
            offset = min(item.source_message_id for item in batch)
            yield TelegramHistoryPage(batch, offset, True)


def gateway(count: int = 6) -> ResumableGateway:
    return ResumableGateway(
        (),
        items=tuple(
            message(index, NOW - timedelta(hours=1)) for index in range(count, 0, -1)
        ),
    )


def test_failed_page_retries_only_unacknowledged_cursor() -> None:
    source = gateway()
    source.fail_at[5] = [TransientOperationError()]
    store = Checkpoints()
    result = run(
        replace(crawler(source, FakeRepository(), NOW), checkpoints=store).execute(
            -1001, correlation_id="page-retry"
        )
    )
    assert result.created == 6
    assert result.already_existing == 0
    assert result.retries == 1
    assert source.fetched == [0, 5, 5, 3]
    assert store.state is not None
    assert store.state.completed


def test_page_limit_never_promotes_high_id_and_restart_resumes_older_pages() -> None:
    source, repository, store = gateway(), FakeRepository(), Checkpoints()
    use_case = replace(crawler(source, repository, NOW), checkpoints=store, max_pages=1)
    first = run(use_case.execute(-1001, correlation_id="limited"))
    assert first.created == 2
    assert not first.completed
    assert store.state is not None
    assert not store.state.completed
    assert store.state.previous_high_message_id == 0
    second = run(use_case.execute(-1001, correlation_id="resume"))
    third = run(use_case.execute(-1001, correlation_id="resume"))
    fourth = run(use_case.execute(-1001, correlation_id="resume"))
    assert second.created == third.created == 2
    assert fourth.completed
    assert source.fetched == [0, 5, 3]
    before = len(source.fetched)
    unchanged = run(use_case.execute(-1001, correlation_id="unchanged"))
    assert unchanged.completed
    assert len(source.fetched) - before == 1
    assert len(repository.posts) == 6


def test_crash_after_completed_page_never_replays_that_page() -> None:
    source, repository, store = gateway(), FakeRepository(), Checkpoints()
    base = crawler(source, repository, NOW)

    class Crash:
        async def execute(
            self, item: TelegramTextMessage, *, correlation_id: str
        ) -> IngestionResult:
            if item.source_message_id == 4:
                raise asyncio.CancelledError
            return await base.ingestor.execute(item, correlation_id=correlation_id)

    with pytest.raises(asyncio.CancelledError):
        run(
            replace(base, checkpoints=store, ingestor=Crash()).execute(
                -1001, correlation_id="crash"
            )
        )
    assert store.state is not None
    assert store.state.offset_message_id == 5
    resumed = run(
        replace(base, checkpoints=store).execute(-1001, correlation_id="restart")
    )
    assert resumed.created == 4
    assert resumed.already_existing == 0
    assert len(repository.posts) == 6


@pytest.mark.parametrize("permanent", [False, True])
def test_individual_failure_isolated_without_skipping_unresolved_work(
    permanent: bool,
) -> None:
    source, repository, store = gateway(4), FakeRepository(), Checkpoints()
    base = crawler(source, repository, NOW)

    class Failure:
        async def execute(
            self, item: TelegramTextMessage, *, correlation_id: str
        ) -> IngestionResult:
            if item.source_message_id == 4:
                if permanent:
                    raise PermanentOperationError
                raise TransientOperationError
            return await base.ingestor.execute(item, correlation_id=correlation_id)

    failed = run(
        replace(base, checkpoints=store, ingestor=Failure()).execute(
            -1001, correlation_id="failure"
        )
    )
    assert failed.failed == 1
    assert store.state is not None
    if permanent:
        assert failed.created == 3
        assert failed.completed
        assert store.failures == {4: "permanent"}
    else:
        assert failed.created == 1
        assert not failed.completed
        assert store.state.offset_message_id == 0
        resumed = run(
            replace(base, checkpoints=store).execute(-1001, correlation_id="resume")
        )
        assert resumed.created == 3
        assert resumed.completed
    assert len(repository.posts) == (3 if permanent else 4)


def test_unordered_pages_do_not_stop_at_old_timestamp() -> None:
    source = FakeHistoryGateway(
        (
            TelegramHistoryPage((message(5, NOW - timedelta(days=1)),), 5, False),
            TelegramHistoryPage((message(2, NOW - timedelta(hours=1)),), 2, False),
        )
    )
    store = Checkpoints()
    result = run(
        replace(crawler(source, FakeRepository(), NOW), checkpoints=store).execute(
            -1001, correlation_id="unordered"
        )
    )
    assert result.created == 1
    assert result.completed
    assert store.state is not None
    assert not store.state.ordered


def test_ordered_history_stops_after_boundary_page() -> None:
    source = gateway(2)
    source.items = (
        message(2, NOW - timedelta(hours=1)),
        message(1, NOW - timedelta(days=1)),
    )
    store = Checkpoints()
    result = run(
        replace(crawler(source, FakeRepository(), NOW), checkpoints=store).execute(
            -1001, correlation_id="boundary"
        )
    )
    assert result.created == 1
    assert result.skipped_outside_interval == 1
    assert result.page_requests == 1
    assert result.completed


@pytest.mark.parametrize(("wait", "allowed"), [(7, True), (61, False)])
def test_floodwait_uses_actual_lower_bound_or_defers(wait: int, allowed: bool) -> None:
    source = FakeHistoryGateway((), failures=[TelegramRateLimitError(wait)])
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    use_case = replace(
        crawler(source, FakeRepository(), NOW),
        sleeper=sleep,
        retry_policy=RetryPolicy(2, 1, 2),
        maximum_flood_wait_seconds=60,
    )
    if allowed:
        result = run(use_case.execute(-1001, correlation_id="flood"))
        assert result.retries == 1
        assert delays == [7]
    else:
        with pytest.raises(TelegramRateLimitError):
            run(use_case.execute(-1001, correlation_id="flood"))
        assert delays == []
        assert len(source.queries) == 1


def test_shutdown_during_retry_sleep_preserves_checkpoint() -> None:
    async def scenario() -> None:
        source = FakeHistoryGateway((), failures=[TransientOperationError()])
        store = Checkpoints()
        entered = asyncio.Event()

        async def sleep(_delay: float) -> None:
            entered.set()
            await asyncio.Event().wait()

        use_case = replace(
            crawler(source, FakeRepository(), NOW), checkpoints=store, sleeper=sleep
        )
        task = asyncio.create_task(use_case.execute(-1001, correlation_id="shutdown"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert store.state is not None
        assert store.state.offset_message_id == 0
        assert not store.state.completed

    run(scenario())


def test_old_interrupted_snapshot_completes_then_catches_up_before_live_boundary() -> (
    None
):
    old_end = NOW - timedelta(hours=2)
    source, repository = gateway(2), FakeRepository()
    source.items = (
        message(3, NOW - timedelta(hours=1)),
        message(2, old_end - timedelta(minutes=1)),
    )
    store = Checkpoints(HistoryCheckpoint(-1001, NOW - timedelta(hours=10), old_end))
    result = run(
        replace(crawler(source, repository, NOW), checkpoints=store).execute(
            -1001, correlation_id="catchup"
        )
    )
    assert result.completed
    assert {identity.source_message_id for identity in repository.posts} == {2, 3}
    assert len(source.queries) == 2
    assert store.state is not None
    assert store.state.end_exclusive == NOW


def test_classified_retry_is_bounded_exponential_jittered_and_aggregated() -> None:
    source = FakeHistoryGateway(
        (), failures=[TransientOperationError(), TransientOperationError()]
    )
    delays: list[float] = []
    logger = RecordingLogger()

    async def sleep(delay: float) -> None:
        delays.append(delay)

    use_case = replace(
        crawler(source, FakeRepository(), NOW),
        sleeper=sleep,
        logger=logger,
        retry_policy=RetryPolicy(3, 2, 10, jitter_ratio=0.2),
        jitter_source=lambda: 0,
    )
    result = run(use_case.execute(-1001, correlation_id="jitter"))
    assert delays == [1.6, 3.2]
    assert result.retries == 2
    assert result.page_requests == 3
    assert result.duration_seconds >= 0
    summaries = [
        fields for name, fields in logger.events if name == "history_crawl_summary"
    ]
    assert len(summaries) == 1
    assert summaries[0] is not None
    assert summaries[0]["retries"] == 2


def test_exhausted_transient_page_does_not_advance_checkpoint() -> None:
    source, store = gateway(), Checkpoints()
    source.fail_at[5] = [TransientOperationError(), TransientOperationError()]
    use_case = replace(crawler(source, FakeRepository(), NOW), checkpoints=store)
    with pytest.raises(TransientOperationError):
        run(use_case.execute(-1001, correlation_id="exhausted"))
    assert store.state is not None
    assert store.state.offset_message_id == 5
    assert not store.state.completed
    assert source.fetched == [0, 5, 5]


def test_cancellation_retained_by_adapter_is_not_recorded_as_permanent() -> None:
    failure = RuntimeError("wrapper")
    failure.__cause__ = asyncio.CancelledError()
    source = FakeHistoryGateway((), failures=[failure])
    store = Checkpoints()
    with pytest.raises(asyncio.CancelledError):
        run(
            replace(crawler(source, FakeRepository(), NOW), checkpoints=store).execute(
                -1001, correlation_id="wrapped-cancellation"
            )
        )
    assert not store.failures


def test_album_hold_survives_page_limit_and_releases_only_completed_snapshot() -> None:
    source, repository, store = gateway(), FakeRepository(), Checkpoints()
    base = crawler(source, repository, NOW)
    holds: list[tuple[bool, datetime | None]] = []

    class AlbumIngestor:
        async def execute(
            self, item: TelegramTextMessage, *, correlation_id: str
        ) -> IngestionResult:
            return await base.ingestor.execute(item, correlation_id=correlation_id)

        async def set_history_hold(
            self,
            _source_channel_id: int,
            *,
            held: bool,
            snapshot_end: datetime | None = None,
        ) -> None:
            holds.append((held, snapshot_end))

    use_case = replace(base, checkpoints=store, ingestor=AlbumIngestor(), max_pages=1)
    limited = run(use_case.execute(-1001, correlation_id="partial-album"))
    assert not limited.completed
    assert holds == [(True, NOW)]
    completed = run(
        replace(use_case, max_pages=4).execute(-1001, correlation_id="resume-album")
    )
    assert completed.completed
    assert holds == [(True, NOW), (True, NOW), (False, NOW)]
    assert len(repository.posts) == 6
