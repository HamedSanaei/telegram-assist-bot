"""Verify bounded idempotent media download orchestration."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from telegram_assist_bot.application.download_post_media import (
    DownloadPostMedia,
    calculate_media_expiration,
)
from telegram_assist_bot.application.ports import (
    MediaDownloadSpec,
    MediaRateLimitError,
    MediaTransientError,
)
from telegram_assist_bot.application.ports.media import MediaPermanentError
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from tests.unit.application.m2_fakes import FakePreparationRepository


def test_calculates_exact_utc_media_expiration_from_fixed_registration_time() -> None:
    registered = datetime(
        2026, 7, 27, 12, 30, tzinfo=timezone(timedelta(hours=3, minutes=30))
    )

    assert calculate_media_expiration(registered, timedelta(days=2)) == datetime(
        2026, 7, 29, 9, 0, tzinfo=UTC
    )


class Source:
    """Return a fresh synthetic stream and count opens."""

    def __init__(self) -> None:
        self.opens = 0

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        self.opens += 1
        assert opaque_reference == "opaque"

        async def stream() -> AsyncIterator[bytes]:
            yield "سلام 😀".encode()

        return stream()


class FailingRepository(FakePreparationRepository):
    """Fail one metadata commit after the file has become durable."""

    def __init__(self) -> None:
        super().__init__()
        self.fail_once = True

    async def save_media_if_absent(self, media: StoredMedia) -> StoredMedia:
        if self.fail_once:
            self.fail_once = False
            raise MediaTransientError("synthetic metadata failure")
        return await super().save_media_if_absent(media)


def test_download_round_trip_filename_and_reuse(tmp_path: Path) -> None:
    source = Source()
    repository = FakePreparationRepository()
    use_case = DownloadPostMedia(
        source,
        LocalMediaStorage(tmp_path),
        repository,
        maximum_bytes=100,
        timeout_seconds=1,
    )
    spec = MediaDownloadSpec(
        MediaIdentity(-100, 7),
        MediaType.PHOTO,
        "opaque",
        "image/jpeg",
        "../عکس 😀.jpg",
        datetime.now(UTC) + timedelta(days=14),
    )

    async def scenario() -> None:
        first = await use_case.execute(spec)
        second = await use_case.execute(spec)
        assert first == second
        assert first.original_filename == "_عکس 😀.jpg"
        assert source.opens == 1

    asyncio.run(scenario())


def test_file_commit_database_failure_recovers_without_truncation(
    tmp_path: Path,
) -> None:
    source = Source()
    repository = FailingRepository()
    storage = LocalMediaStorage(tmp_path)
    use_case = DownloadPostMedia(
        source,
        storage,
        repository,
        maximum_bytes=100,
        timeout_seconds=1,
        maximum_attempts=1,
    )
    spec = MediaDownloadSpec(
        MediaIdentity(-100, 8),
        MediaType.DOCUMENT,
        "opaque",
        None,
        "فایل.bin",
        datetime.now(UTC) + timedelta(days=14),
    )

    async def scenario() -> None:
        with pytest.raises(MediaTransientError, match="metadata"):
            await use_case.execute(spec)
        files = tuple((tmp_path / "sha256").rglob("*"))
        committed = next(path for path in files if path.is_file())
        before = committed.read_bytes()
        # Simulate an explicit new operation budget after the exhausted failure.
        repository.failures.pop(spec.identity.key)
        recovered = await use_case.execute(spec)
        assert committed.read_bytes() == before
        assert recovered.size_bytes == len(before)

    asyncio.run(scenario())


def test_timeout_and_cancellation_cleanup_partial(tmp_path: Path) -> None:
    class BlockingSource:
        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            del opaque_reference

            async def stream() -> AsyncIterator[bytes]:
                yield b"first"
                await asyncio.Event().wait()

            return stream()

    storage = LocalMediaStorage(tmp_path)
    repository = FakePreparationRepository()
    spec = MediaDownloadSpec(
        MediaIdentity(-100, 9),
        MediaType.VIDEO,
        "opaque",
        None,
        None,
        datetime.now(UTC) + timedelta(days=14),
    )

    async def scenario() -> None:
        timed = DownloadPostMedia(
            BlockingSource(),
            storage,
            repository,
            maximum_bytes=100,
            timeout_seconds=0.01,
            maximum_attempts=1,
        )
        with pytest.raises(TimeoutError):
            await timed.execute(spec)
        task = asyncio.create_task(
            DownloadPostMedia(
                BlockingSource(),
                storage,
                repository,
                maximum_bytes=100,
                timeout_seconds=10,
            ).execute(replace(spec, identity=MediaIdentity(-100, 90)))
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not tuple((tmp_path / ".tmp").glob("*.partial"))

    asyncio.run(scenario())


def test_rate_limit_above_wait_budget_defers_without_shortening_floodwait(
    tmp_path: Path,
) -> None:
    class RateLimitedSource(Source):
        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            self.opens += 1
            if self.opens == 1:
                raise MediaRateLimitError(30)
            assert opaque_reference == "opaque"

            async def stream() -> AsyncIterator[bytes]:
                yield b"media"

            return stream()

    async def scenario() -> None:
        delays: list[float] = []
        source = RateLimitedSource()
        use_case = DownloadPostMedia(
            source,
            LocalMediaStorage(tmp_path),
            FakePreparationRepository(),
            maximum_bytes=100,
            timeout_seconds=1,
            maximum_attempts=2,
            maximum_rate_limit_delay_seconds=2,
            sleeper=lambda delay: _record_delay(delay, delays),
        )
        with pytest.raises(MediaRateLimitError):
            await use_case.execute(
                MediaDownloadSpec(
                    MediaIdentity(-100, 10),
                    MediaType.PHOTO,
                    "opaque",
                    None,
                    None,
                    datetime.now(UTC) + timedelta(days=14),
                )
            )
        assert delays == []
        assert source.opens == 1

    async def _record_delay(delay: float, delays: list[float]) -> None:
        delays.append(delay)

    asyncio.run(scenario())

    for invalid in (-1, 1.5):
        with pytest.raises(ValueError, match="non-negative integer"):
            MediaRateLimitError(invalid)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="bounds"):
        DownloadPostMedia(
            Source(),
            LocalMediaStorage(tmp_path / "invalid"),
            FakePreparationRepository(),
            maximum_bytes=1,
            timeout_seconds=1,
            maximum_rate_limit_delay_seconds=-1,
        )


def test_transient_attempts_are_bounded_and_deferred_state_blocks_hot_replay(
    tmp_path: Path,
) -> None:
    class UnavailableSource(Source):
        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            del opaque_reference
            self.opens += 1
            raise MediaTransientError("Synthetic temporary outage.")

    async def scenario() -> None:
        source = UnavailableSource()
        repository = FakePreparationRepository()
        delays: list[float] = []

        async def sleep(delay: float) -> None:
            delays.append(delay)

        downloader = DownloadPostMedia(
            source,
            LocalMediaStorage(tmp_path),
            repository,
            maximum_bytes=100,
            timeout_seconds=1,
            maximum_attempts=2,
            sleeper=sleep,
        )
        spec = MediaDownloadSpec(
            MediaIdentity(-100, 11),
            MediaType.DOCUMENT,
            "opaque",
            None,
            None,
            datetime.now(UTC) + timedelta(days=2),
        )
        with pytest.raises(MediaTransientError):
            await downloader.execute(spec)
        assert source.opens == 2
        assert len(delays) == 1
        failure = await repository.get_media_failure(spec.identity)
        assert failure is not None
        assert failure.attempts == 2
        assert failure.category == "retry_exhausted"
        assert failure.retry_at is None
        with pytest.raises(MediaPermanentError):
            await downloader.execute(spec)
        assert source.opens == 2

    asyncio.run(scenario())


def test_metadata_retry_resumes_commit_without_redownloading(tmp_path: Path) -> None:
    async def scenario() -> None:
        source = Source()
        repository = FailingRepository()

        async def sleep(delay: float) -> None:
            del delay

        downloader = DownloadPostMedia(
            source,
            LocalMediaStorage(tmp_path),
            repository,
            maximum_bytes=100,
            timeout_seconds=1,
            maximum_attempts=2,
            sleeper=sleep,
        )
        spec = MediaDownloadSpec(
            MediaIdentity(-100, 12),
            MediaType.DOCUMENT,
            "opaque",
            None,
            None,
            datetime.now(UTC) + timedelta(days=2),
        )
        media = await downloader.execute(spec)
        assert source.opens == 1
        assert media.size_bytes == len("سلام 😀".encode())
        assert await repository.get_media_failure(spec.identity) is None

    asyncio.run(scenario())


@pytest.mark.parametrize("cancel_waiter", [False, True])
def test_concurrent_same_item_waits_without_reinterpreting_in_progress_as_floodwait(
    tmp_path: Path,
    cancel_waiter: bool,
) -> None:
    class HoldingSource(Source):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            assert opaque_reference == "opaque"
            self.opens += 1
            self.started.set()

            async def stream() -> AsyncIterator[bytes]:
                yield b"first"
                await self.release.wait()
                yield b"second"

            return stream()

    async def scenario() -> None:
        source, repository = HoldingSource(), FakePreparationRepository()
        storage = LocalMediaStorage(tmp_path)
        downloader = DownloadPostMedia(
            source,
            storage,
            repository,
            maximum_bytes=100,
            timeout_seconds=5,
        )
        spec = MediaDownloadSpec(
            MediaIdentity(-100, 13),
            MediaType.DOCUMENT,
            "opaque",
            None,
            None,
            datetime.now(UTC) + timedelta(days=2),
        )
        owner = asyncio.create_task(downloader.execute(spec))
        await source.started.wait()
        waiter = asyncio.create_task(downloader.execute(spec))
        await asyncio.sleep(0)
        if cancel_waiter:
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        source.release.set()
        first = await owner
        second = await downloader.execute(spec) if cancel_waiter else await waiter
        assert first == second
        assert source.opens == 1
        assert len(repository.media) == 1
        assert (tmp_path / first.storage_path).read_bytes() == b"firstsecond"

    asyncio.run(scenario())
