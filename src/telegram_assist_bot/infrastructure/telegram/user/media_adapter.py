"""Resolve canonical message references and stream Telethon media bytes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from telethon.errors import (  # type: ignore[import-untyped]
    ChannelPrivateError,
    FloodWaitError,
    MessageIdInvalidError,
)

from telegram_assist_bot.application.ports import (
    MediaPermanentError,
    MediaRateLimitError,
    MediaTransientError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class TelethonMediaClient(Protocol):
    """Describe only Telethon operations needed for media download."""

    async def get_messages(self, entity: int, *, ids: int) -> object:
        """Resolve one source message."""
        ...

    def iter_download(self, file: object, *, chunk_size: int) -> AsyncIterator[bytes]:
        """Stream one media object in bounded chunks."""
        ...


class TelethonMediaSource:
    """Re-resolve persisted source identity before each streamed attempt."""

    def __init__(
        self, client: TelethonMediaClient, *, chunk_size: int = 64 * 1024
    ) -> None:
        """Initialize a bounded chunk size and injected client."""
        if not 4096 <= chunk_size <= 1024 * 1024:
            raise ValueError("Telegram media chunk size is invalid.")
        self._client = client
        self._chunk_size = chunk_size

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        """Resolve source identity without retaining an expiring file reference."""
        try:
            channel_text, message_text, item_text = opaque_reference.split(":", 2)
            channel_id, message_id, item_index = (
                int(channel_text),
                int(message_text),
                int(item_text),
            )
        except (TypeError, ValueError):
            raise MediaPermanentError("Telegram media reference is invalid.") from None
        if channel_id == 0 or message_id <= 0 or item_index != 0:
            raise MediaPermanentError("Telegram media reference is invalid.")
        try:
            message = await self._client.get_messages(channel_id, ids=message_id)
        except FloodWaitError as error:
            raise MediaRateLimitError(max(0, int(error.seconds))) from error
        except (ChannelPrivateError, MessageIdInvalidError) as error:
            raise MediaPermanentError("Telegram media is inaccessible.") from error
        except Exception as error:
            raise MediaTransientError(
                "Telegram media could not be resolved."
            ) from error
        media = getattr(message, "media", None)
        if media is None:
            raise MediaPermanentError("Telegram media no longer exists.")
        provider_stream = self._client.iter_download(media, chunk_size=self._chunk_size)

        async def stream() -> AsyncIterator[bytes]:
            try:
                async for chunk in provider_stream:
                    yield chunk
            except GeneratorExit:
                raise
            except FloodWaitError as error:
                raise MediaRateLimitError(max(0, int(error.seconds))) from error
            except (ChannelPrivateError, MessageIdInvalidError) as error:
                raise MediaPermanentError("Telegram media is inaccessible.") from error
            except Exception as error:
                raise MediaTransientError(
                    "Telegram media stream was interrupted."
                ) from error

        return stream()
