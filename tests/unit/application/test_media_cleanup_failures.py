"""Retain primary rejection/cancellation while surfacing failed stream cleanup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.download_post_media import DownloadPostMedia
from telegram_assist_bot.application.ports import MediaDownloadSpec
from telegram_assist_bot.application.ports.media import (
    MediaPermanentError,
    MediaTooLargeError,
)
from telegram_assist_bot.domain.media import MediaIdentity, MediaType
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from tests.unit.application.m2_fakes import FakePreparationRepository

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.mark.parametrize("mode", ["oversized", "cancelled", "successful-stream"])
def test_cleanup_failure_cannot_mask_primary_disposition_or_silently_succeed(
    tmp_path: Path,
    mode: str,
) -> None:
    class FaultyStream:
        def __init__(self) -> None:
            self.calls = 0
            self.produced = 0

        def __aiter__(self) -> FaultyStream:
            return self

        async def __anext__(self) -> bytes:
            self.calls += 1
            if self.calls == 1 or (self.calls == 2 and mode == "oversized"):
                self.produced += 60
                return b"x" * 60
            if mode == "cancelled":
                raise asyncio.CancelledError
            raise StopAsyncIteration

        async def aclose(self) -> None:
            raise OSError("Synthetic stream cleanup failure.")

    class Source:
        opens = 0

        async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
            del opaque_reference
            self.opens += 1
            return stream

    async def scenario() -> None:
        repository, source = FakePreparationRepository(), Source()
        downloader = DownloadPostMedia(
            source,
            LocalMediaStorage(tmp_path),
            repository,
            maximum_bytes=100,
            timeout_seconds=1,
        )
        spec = MediaDownloadSpec(
            MediaIdentity(-100, 1),
            MediaType.DOCUMENT,
            "synthetic",
            None,
            None,
            datetime.now(UTC) + timedelta(days=2),
        )
        expected: type[BaseException] = (
            asyncio.CancelledError
            if mode == "cancelled"
            else MediaTooLargeError
            if mode == "oversized"
            else MediaPermanentError
        )
        with pytest.raises(expected) as caught:
            await downloader.execute(spec)
        assert isinstance(caught.value.__cause__, OSError)
        assert stream.produced == (120 if mode == "oversized" else 60)
        assert not tuple((tmp_path / ".tmp").glob("*.partial"))
        assert not repository.media
        failure = await repository.get_media_failure(spec.identity)
        assert failure is not None
        if mode == "cancelled":
            assert failure.category == "attempting"
        else:
            assert failure.category == (
                "oversized" if mode == "oversized" else "permanent"
            )
            with pytest.raises(MediaPermanentError):
                await downloader.execute(spec)
            assert source.opens == 1

    stream = FaultyStream()
    asyncio.run(scenario())
