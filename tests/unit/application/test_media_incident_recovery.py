"""Exercise durable media incident isolation and missing-stage recovery offline."""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.approvals import AuthorizeAdminAction
from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ports import MediaTransientError
from telegram_assist_bot.application.ports.admin import BotUpdate
from telegram_assist_bot.application.prepare_post_pipeline import PreparePostPipeline
from telegram_assist_bot.application.retry_rejected_media import (
    request_rejected_media_retry,
)
from telegram_assist_bot.domain.admin_approval import Administrator, AdminPermission
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from tests.unit.application.test_runtime_ingestion import (
    Clock,
    Source,
    coordinator,
    message,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def administrator() -> AuthorizeAdminAction:
    return AuthorizeAdminAction(
        (
            Administrator(
                7,
                True,
                "admin",
                frozenset({AdminPermission.TOGGLE}),
                frozenset({-200}),
            ),
        )
    )


def test_production_identity_rejection_survives_restart_and_policy_change(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        incident = json.loads(
            (
                Path(__file__).parents[2]
                / "fixtures"
                / "telegram"
                / "production_oversized_history.json"
            ).read_text(encoding="utf-8")
        )
        runtime, repository, source, _ = coordinator(tmp_path, Clock(now))
        runtime.policy = replace(
            runtime.policy,
            sources=(
                replace(
                    runtime.policy.sources[0],
                    source_channel_id=-1001648659094,
                ),
            ),
        )
        fixture = replace(
            message(
                incident["source_message_id"],
                now,
                media_type=MediaType.DOCUMENT,
                caption=incident["caption"],
            ),
            source_channel_id=-1001648659094,
        )
        fixture = replace(
            fixture,
            media=(
                replace(
                    fixture.media[0],
                    size_bytes=incident["declared_size_bytes"],
                ),
            ),
        )
        runtime.downloader = DownloadPostMedia(
            source,
            runtime.storage,
            repository,
            maximum_bytes=100 * 1024 * 1024,
            timeout_seconds=1,
        )
        result = await runtime.execute(fixture, correlation_id="incident")
        assert source.opens == 0
        failure = await repository.get_media_failure(
            MediaIdentity(-1001648659094, 68568)
        )
        assert failure is not None
        assert failure.category == "oversized"
        assert failure.maximum_bytes == 100 * 1024 * 1024
        assert repository.messages[result.post_id.value] == fixture
        runtime.downloader = DownloadPostMedia(
            source,
            runtime.storage,
            repository,
            maximum_bytes=200 * 1024 * 1024,
            timeout_seconds=1,
        )
        await runtime.execute(fixture, correlation_id="restart")
        assert source.opens == 0
        assert result.post_id.value not in repository.ready
        with pytest.raises(PermissionError):
            await request_rejected_media_retry(
                source_channel_id=fixture.source_channel_id,
                source_message_id=fixture.source_message_id,
                update=BotUpdate(8, 8, "private"),
                authorize=administrator(),
                post_repository=runtime.post_repository,
                content_repository=repository,
                clock=runtime.clock,
            )
        assert await request_rejected_media_retry(
            source_channel_id=fixture.source_channel_id,
            source_message_id=fixture.source_message_id,
            update=BotUpdate(7, 7, "private"),
            authorize=administrator(),
            post_repository=runtime.post_repository,
            content_repository=repository,
            clock=runtime.clock,
            destination_ids=(-200,),
        )
        assert await runtime.recover_pending_messages() == 1
        assert source.opens == 1
        assert result.post_id.value in repository.ready
        assert repository.retry_audit[0][1] == 7

    asyncio.run(scenario())


def test_streamed_oversize_closes_iterator_cleans_partial_and_continues(
    tmp_path: Path,
) -> None:
    class OversizedSource(Source):
        closed = False
        yielded = 0

        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            del opaque_reference
            self.opens += 1

            async def stream() -> AsyncIterator[bytes]:
                try:
                    for _ in range(10):
                        self.yielded += 1
                        yield b"x" * 60
                finally:
                    self.closed = True

            return stream()

    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        source = OversizedSource()
        runtime, repository, _, _ = coordinator(tmp_path, Clock(now), source=source)
        fixture = message(1, now, media_type=MediaType.DOCUMENT, caption="اصل")
        fixture = replace(fixture, media=(replace(fixture.media[0], size_bytes=None),))
        await runtime.execute(fixture, correlation_id="oversized-stream")
        assert source.closed
        assert source.yielded == 2
        assert not tuple((tmp_path / "media" / ".tmp").glob("*.partial"))
        await runtime.execute(fixture, correlation_id="repeated")
        await runtime.execute(message(2, now), correlation_id="eligible")
        assert source.opens == 1
        assert len(repository.ready) == 1

    asyncio.run(scenario())


def test_ready_outbox_crash_repairs_without_repeating_media_or_content(
    tmp_path: Path,
) -> None:
    class Outbox:
        def __init__(self) -> None:
            self.calls = 0
            self.deliveries: set[str] = set()

        async def ensure_delivery(self, post_id: str, *, ready_at: datetime) -> bool:
            del ready_at
            self.calls += 1
            if self.calls == 1:
                raise MediaTransientError("Synthetic outbox interruption.")
            self.deliveries.add(post_id)
            return True

    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        runtime, repository, source, logger = coordinator(tmp_path, Clock(now))
        outbox = Outbox()
        runtime.pipeline = PreparePostPipeline(repository, outbox)
        fixture = message(1, now, media_type=MediaType.PHOTO, caption="متن 😀")
        with pytest.raises(MediaTransientError):
            await runtime.execute(fixture, correlation_id="crash")
        before = len(logger.events)
        result = await runtime.execute(fixture, correlation_id="recover")
        assert outbox.deliveries == {result.post_id.value}
        assert source.opens == 1
        assert len(logger.events) == before
        assert runtime.metrics_snapshot()["skipped_completed_messages"] == 1
        await runtime.execute(fixture, correlation_id="complete")
        assert outbox.calls == 2

    asyncio.run(scenario())


def test_rejected_album_blocks_publication_until_authorized_recovery(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        clock = Clock(now)
        runtime, repository, source, _ = coordinator(tmp_path, clock)
        rejected = message(
            1, now, media_type=MediaType.PHOTO, group_id="album", caption="اصل"
        )
        rejected = replace(
            rejected, media=(replace(rejected.media[0], size_bytes=101),)
        )
        eligible = message(2, now, media_type=MediaType.PHOTO, group_id="album")
        await runtime.execute(rejected, correlation_id="reject")
        await runtime.execute(eligible, correlation_id="eligible-member")
        clock.now += timedelta(minutes=1)
        assert await runtime.finalize_due_groups() == 0
        assert not repository.ready
        runtime.downloader = DownloadPostMedia(
            source,
            runtime.storage,
            repository,
            maximum_bytes=200,
            timeout_seconds=1,
        )
        await request_rejected_media_retry(
            source_channel_id=rejected.source_channel_id,
            source_message_id=rejected.source_message_id,
            update=BotUpdate(7, 7, "private"),
            authorize=administrator(),
            post_repository=runtime.post_repository,
            content_repository=repository,
            clock=runtime.clock,
            destination_ids=(-200,),
        )
        assert await runtime.recover_pending_messages() == 1
        clock.now += timedelta(seconds=3)
        assert await runtime.finalize_due_groups() == 1
        assert len(repository.ready) == 1
        assert source.opens == 2

    asyncio.run(scenario())


def test_concurrent_incomplete_delivery_cannot_start_second_download(
    tmp_path: Path,
) -> None:
    class BlockingSource(Source):
        started: asyncio.Event
        release: asyncio.Event

        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            self.started.set()
            await self.release.wait()
            return await super().open(opaque_reference)

    async def scenario() -> None:
        now = datetime(2026, 10, 21, tzinfo=UTC)
        source = BlockingSource()
        source.started, source.release = asyncio.Event(), asyncio.Event()
        runtime, repository, _, _ = coordinator(tmp_path, Clock(now), source=source)
        fixture = message(1, now, media_type=MediaType.PHOTO)
        first = asyncio.create_task(runtime.execute(fixture, correlation_id="first"))
        await source.started.wait()
        with pytest.raises(MediaTransientError):
            await runtime.execute(fixture, correlation_id="overlap")
        source.release.set()
        await first
        assert source.opens == 1
        assert len(repository.ready) == 1

    asyncio.run(scenario())


def test_history_hold_preserves_old_album_without_starving_new_live_album(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        now = datetime(2026, 10, 21, 12, tzinfo=UTC)
        clock = Clock(now)
        runtime, repository, _, _ = coordinator(tmp_path, clock)
        snapshot_end = now - timedelta(minutes=1)
        await runtime.set_history_hold(-100, held=True, snapshot_end=snapshot_end)
        await runtime.execute(
            message(
                1,
                now - timedelta(hours=1),
                media_type=MediaType.PHOTO,
                group_id="historical",
            ),
            correlation_id="historical",
        )
        await runtime.execute(
            message(2, now, media_type=MediaType.PHOTO, group_id="live"),
            correlation_id="live",
        )
        clock.now += timedelta(seconds=3)
        assert await runtime.finalize_due_groups() == 1
        assert len(repository.ready) == 1
        await runtime.set_history_hold(-100, held=False, snapshot_end=snapshot_end)
        # A delayed older crawl cannot reopen the already-completed generation.
        await runtime.set_history_hold(-100, held=True, snapshot_end=snapshot_end)
        assert await runtime.finalize_due_groups() == 1
        assert len(repository.ready) == 2

    asyncio.run(scenario())
