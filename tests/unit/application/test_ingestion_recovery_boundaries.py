"""Cover durable attempt consumption and concurrent authorized recovery boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ports import (
    MediaRateLimitError,
    MediaTransientError,
)
from telegram_assist_bot.application.ports.admin import BotUpdate
from telegram_assist_bot.application.retry_rejected_media import (
    request_rejected_media_retry,
)
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from tests.unit.application.m2_fakes import FakePreparationRepository
from tests.unit.application.test_media_incident_recovery import administrator
from tests.unit.application.test_runtime_ingestion import (
    Clock,
    Source,
    coordinator,
    message,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from telegram_assist_bot.domain.posts import PostId


@pytest.mark.parametrize("attempt_limit", [1, 2])
def test_crash_consumes_attempt_and_restart_cannot_renew_exhausted_budget(
    tmp_path: Path,
    attempt_limit: int,
) -> None:
    class CrashSource(Source):
        def __init__(self) -> None:
            super().__init__()
            self.stage: str = "block"
            self.entered = asyncio.Event()

        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            if self.stage == "block":
                self.opens += 1
                self.entered.set()
                await asyncio.Event().wait()
            if self.stage == "fail":
                self.opens += 1
                raise MediaTransientError("Synthetic provider outage.")
            return await super().open(opaque_reference)

    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        source = CrashSource()
        runtime, repository, _, _ = coordinator(tmp_path, clock, source=source)
        runtime.downloader = DownloadPostMedia(
            source,
            runtime.storage,
            repository,
            maximum_bytes=100,
            timeout_seconds=1,
            maximum_attempts=attempt_limit,
            clock=clock,
        )
        fixture = message(1, clock.now, media_type=MediaType.DOCUMENT)
        task = asyncio.create_task(
            runtime.execute(fixture, correlation_id="interrupted")
        )
        await source.entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        identity = MediaIdentity(-100, 1)
        failure = await repository.get_media_failure(identity)
        assert failure is not None
        assert failure.attempts == 1
        assert failure.category == "attempting"
        clock.now += timedelta(hours=1)
        source.stage = "fail"
        restarted = replace(
            runtime,
            downloader=DownloadPostMedia(
                source,
                runtime.storage,
                repository,
                maximum_bytes=100,
                timeout_seconds=1,
                maximum_attempts=attempt_limit,
                clock=clock,
            ),
        )
        if attempt_limit == 1:
            await restarted.execute(fixture, correlation_id="spent-before-crash")
        else:
            with pytest.raises(MediaTransientError):
                await restarted.execute(fixture, correlation_id="remaining-attempt")
        failure = await repository.get_media_failure(identity)
        assert failure is not None
        assert failure.attempts == attempt_limit
        assert failure.category == "retry_exhausted"
        assert failure.last_failure_category == (
            "attempting" if attempt_limit == 1 else "transient"
        )
        for _ in range(3):
            await restarted.execute(fixture, correlation_id="replayed")
            assert await restarted.recover_pending_messages() == 0
        assert source.opens == attempt_limit
        assert await request_rejected_media_retry(
            source_channel_id=-100,
            source_message_id=1,
            update=BotUpdate(7, 7, "private"),
            authorize=administrator(),
            post_repository=runtime.post_repository,
            content_repository=repository,
            clock=clock,
            destination_ids=(-200,),
        )
        source.stage = "healthy"
        assert await restarted.recover_pending_messages() == 1
        assert source.opens == attempt_limit + 1
        assert len(repository.ready) == 1
        assert repository.retry_audit[0][3].attempts == attempt_limit

    asyncio.run(scenario())


def test_old_recovery_cannot_acknowledge_a_new_authorized_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        runtime, repository, source, _ = coordinator(tmp_path, clock)
        fixture = message(1, clock.now, media_type=MediaType.DOCUMENT)
        fixture = replace(fixture, media=(replace(fixture.media[0], size_bytes=101),))
        result = await runtime.execute(fixture, correlation_id="rejected")

        async def request() -> bool:
            return await request_rejected_media_retry(
                source_channel_id=-100,
                source_message_id=1,
                update=BotUpdate(7, 7, "private"),
                authorize=administrator(),
                post_repository=runtime.post_repository,
                content_repository=repository,
                clock=clock,
            )

        assert await request()
        release = repository.release_ingestion
        armed = True

        async def release_and_request(
            _self: FakePreparationRepository,
            post_id: PostId,
            *,
            owner: str,
        ) -> None:
            nonlocal armed
            await release(post_id, owner=owner)
            if armed and post_id.value in repository.rejected_posts:
                armed = False
                assert await request()

        monkeypatch.setattr(
            FakePreparationRepository, "release_ingestion", release_and_request
        )
        await runtime.recover_pending_messages()
        assert not armed
        assert result.post_id.value in repository.pending
        assert result.post_id.value not in repository.rejected_posts
        runtime.downloader = DownloadPostMedia(
            source,
            runtime.storage,
            repository,
            maximum_bytes=200,
            timeout_seconds=1,
            clock=clock,
        )
        assert await runtime.recover_pending_messages() == 1
        assert result.post_id.value in repository.ready
        assert source.opens == 1

    asyncio.run(scenario())


def test_authorized_reset_cannot_shorten_a_terminal_floodwait(tmp_path: Path) -> None:
    class FloodSource(Source):
        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            del opaque_reference
            self.opens += 1
            raise MediaRateLimitError(30)

    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        source = FloodSource()
        runtime, repository, _, _ = coordinator(tmp_path, clock, source=source)
        runtime.downloader = DownloadPostMedia(
            source,
            runtime.storage,
            repository,
            maximum_bytes=100,
            timeout_seconds=1,
            maximum_attempts=1,
            clock=clock,
        )
        fixture = message(1, clock.now, media_type=MediaType.DOCUMENT)
        with pytest.raises(MediaRateLimitError):
            await runtime.execute(fixture, correlation_id="flood")
        with pytest.raises(MediaRateLimitError) as caught:
            await request_rejected_media_retry(
                source_channel_id=-100,
                source_message_id=1,
                update=BotUpdate(7, 7, "private"),
                authorize=administrator(),
                post_repository=runtime.post_repository,
                content_repository=repository,
                clock=clock,
            )
        assert caught.value.retry_after_seconds >= 30
        assert source.opens == 1
        clock.now += timedelta(seconds=31)
        assert await request_rejected_media_retry(
            source_channel_id=-100,
            source_message_id=1,
            update=BotUpdate(7, 7, "private"),
            authorize=administrator(),
            post_repository=runtime.post_repository,
            content_repository=repository,
            clock=clock,
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("rejection", "expected"),
    [
        ("destination", PermissionError),
        ("missing", ValueError),
        ("busy", MediaTransientError),
    ],
)
def test_explicit_retry_failure_cannot_clear_rejection_or_steal_work(
    tmp_path: Path,
    rejection: str,
    expected: type[Exception],
) -> None:
    async def scenario() -> None:
        clock = Clock(datetime(2026, 10, 21, tzinfo=UTC))
        runtime, repository, source, _ = coordinator(tmp_path, clock)
        fixture = message(1, clock.now, media_type=MediaType.DOCUMENT)
        fixture = replace(fixture, media=(replace(fixture.media[0], size_bytes=101),))
        result = await runtime.execute(fixture, correlation_id="rejected")
        if rejection == "busy":
            repository.leases[result.post_id.value] = (
                "processing",
                clock.now + timedelta(minutes=1),
            )
        with pytest.raises(expected):
            await request_rejected_media_retry(
                source_channel_id=-100,
                source_message_id=99999 if rejection == "missing" else 1,
                update=BotUpdate(7, 7, "private"),
                authorize=administrator(),
                post_repository=runtime.post_repository,
                content_repository=repository,
                clock=clock,
                destination_ids=(-201,) if rejection == "destination" else (-200,),
            )
        assert result.post_id.value in repository.rejected_posts
        failure = await repository.get_media_failure(MediaIdentity(-100, 1))
        assert failure is not None
        assert failure.category == "oversized"
        assert not repository.retry_audit
        assert source.opens == 0
        if rejection == "busy":
            assert repository.leases[result.post_id.value][0] == "processing"

    asyncio.run(scenario())
