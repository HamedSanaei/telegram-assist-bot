"""Verify Telegram media re-resolution and streaming error mapping."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass

import pytest

from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    MediaTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user.media_adapter import (
    TelethonMediaSource,
)


@dataclass
class Message:
    media: object | None


class Client:
    """Synthetic media client."""

    def __init__(
        self, message: object = Message(object()), *, failure: bool = False
    ) -> None:
        self.message, self.failure = message, failure

    async def get_messages(self, entity: int, *, ids: int) -> object:
        assert (entity, ids) == (-100, 7)
        if self.failure:
            raise OSError
        return self.message

    def iter_download(self, file: object, *, chunk_size: int) -> AsyncIterator[bytes]:
        assert file is not None
        assert chunk_size == 4096

        async def stream() -> AsyncIterator[bytes]:
            yield b"data"

        return stream()


def test_stream_and_error_mapping() -> None:
    async def scenario() -> None:
        source = TelethonMediaSource(Client(), chunk_size=4096)
        assert [chunk async for chunk in await source.open("-100:7:0")] == [b"data"]
        for value in ("bad", "0:7:0", "-100:0:0", "-100:7:1"):
            with pytest.raises(MediaPermanentError, match="invalid"):
                await source.open(value)
        with pytest.raises(MediaPermanentError, match="no longer"):
            await TelethonMediaSource(Client(Message(None))).open("-100:7:0")
        with pytest.raises(MediaTransientError, match="resolved"):
            await TelethonMediaSource(Client(failure=True)).open("-100:7:0")

    asyncio.run(scenario())


def test_chunk_bounds() -> None:
    with pytest.raises(ValueError, match="chunk size"):
        TelethonMediaSource(Client(), chunk_size=1)
