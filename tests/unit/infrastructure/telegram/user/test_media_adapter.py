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
    photo: object | None = None
    document: object | None = None


class MessageMediaPhoto: ...


class MessageMediaDocument: ...


class MessageMediaWebPage: ...


class Client:
    """Synthetic media client."""

    def __init__(
        self,
        message: object | None = None,
        *,
        failure: bool = False,
        stream_failure: bool = False,
        start_failure: Exception | None = None,
    ) -> None:
        self.message = (
            message
            if message is not None
            else Message(MessageMediaPhoto(), photo=object())
        )
        self.failure = failure
        self.stream_failure = stream_failure
        self.start_failure = start_failure
        self.downloaded: list[object] = []

    async def get_messages(self, entity: int, *, ids: int) -> object:
        assert (entity, ids) == (-100, 7)
        if self.failure:
            raise OSError
        return self.message

    def iter_download(self, file: object, *, chunk_size: int) -> AsyncIterator[bytes]:
        assert file is not None
        assert chunk_size == 4096
        if self.start_failure is not None:
            raise self.start_failure
        self.downloaded.append(file)

        async def stream() -> AsyncIterator[bytes]:
            if self.stream_failure:
                raise OSError("synthetic provider detail")
            yield b"data"

        return stream()


def test_stream_and_error_mapping() -> None:
    async def scenario() -> None:
        photo = object()
        photo_client = Client(Message(MessageMediaPhoto(), photo=photo))
        source = TelethonMediaSource(photo_client, chunk_size=4096)
        assert [chunk async for chunk in await source.open("-100:7:0")] == [b"data"]
        assert photo_client.downloaded == [photo]
        document = object()
        document_client = Client(Message(MessageMediaDocument(), document=document))
        document_source = TelethonMediaSource(document_client, chunk_size=4096)
        assert [chunk async for chunk in await document_source.open("-100:7:0")] == [
            b"data"
        ]
        assert document_client.downloaded == [document]
        for value in ("bad", "0:7:0", "-100:0:0", "-100:7:1"):
            with pytest.raises(MediaPermanentError, match="invalid"):
                await source.open(value)
        with pytest.raises(MediaPermanentError, match="no longer"):
            await TelethonMediaSource(Client(Message(None))).open("-100:7:0")
        webpage = Client(
            Message(
                MessageMediaWebPage(),
                document=object(),
            )
        )
        with pytest.raises(MediaPermanentError, match="no longer"):
            await TelethonMediaSource(webpage).open("-100:7:0")
        assert webpage.downloaded == []
        with pytest.raises(MediaTransientError, match="resolved"):
            await TelethonMediaSource(Client(failure=True)).open("-100:7:0")
        with pytest.raises(MediaPermanentError, match="invalid"):
            await TelethonMediaSource(
                Client(start_failure=TypeError("provider detail")),
                chunk_size=4096,
            ).open("-100:7:0")
        with pytest.raises(MediaTransientError, match="could not start"):
            await TelethonMediaSource(
                Client(start_failure=OSError("provider detail")),
                chunk_size=4096,
            ).open("-100:7:0")
        interrupted = await TelethonMediaSource(
            Client(stream_failure=True), chunk_size=4096
        ).open("-100:7:0")
        with pytest.raises(MediaTransientError, match="interrupted"):
            await anext(interrupted)

    asyncio.run(scenario())


def test_chunk_bounds() -> None:
    with pytest.raises(ValueError, match="chunk size"):
        TelethonMediaSource(Client(), chunk_size=1)
