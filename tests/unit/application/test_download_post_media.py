"""Verify bounded idempotent media download orchestration."""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ports import (
    MediaDownloadSpec,
    MediaRateLimitError,
    MediaTransientError,
)
from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from tests.unit.application.m2_fakes import FakePreparationRepository


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
            ).execute(spec)
        )
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not tuple((tmp_path / ".tmp").glob("*.partial"))

    asyncio.run(scenario())


def test_rate_limit_wait_is_capped_and_retry_is_bounded(tmp_path: Path) -> None:
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
        assert delays == [2]
        assert source.opens == 2

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
