"""Telethon User API adapter for destination text, media, and albums."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Final, Protocol

from telethon import errors, types  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import PublisherError
from telegram_assist_bot.domain import PublicationFailureCategory, PublishedMessage

if TYPE_CHECKING:
    from collections.abc import Callable

    from telegram_assist_bot.application.ports import PublicationPayload
    from telegram_assist_bot.domain.posts import TelegramEntity


class TelethonPublisherClient(Protocol):
    """Describe only SDK calls required by destination publication."""

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        """Send one text message."""
        ...

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        """Send one media item or album."""
        ...


_ENTITY_TYPES: Final[dict[str, Callable[..., object]]] = {
    "bold": types.MessageEntityBold,
    "italic": types.MessageEntityItalic,
    "underline": types.MessageEntityUnderline,
    "strike": types.MessageEntityStrike,
    "code": types.MessageEntityCode,
    "pre": types.MessageEntityPre,
    "url": types.MessageEntityUrl,
    "text_url": types.MessageEntityTextUrl,
    "mention": types.MessageEntityMention,
    "hashtag": types.MessageEntityHashtag,
    "cashtag": types.MessageEntityCashtag,
    "email": types.MessageEntityEmail,
    "phone": types.MessageEntityPhone,
    "blockquote": types.MessageEntityBlockquote,
    "spoiler": types.MessageEntitySpoiler,
}


def _map_entity(entity: TelegramEntity) -> object:
    """Map one complete application entity without changing UTF-16 offsets."""
    if entity.entity_type == "custom_emoji":
        if entity.custom_emoji_id is None:
            raise ValueError("Custom Emoji document identity is missing.")
        return types.MessageEntityCustomEmoji(
            entity.offset_utf16, entity.length_utf16, int(entity.custom_emoji_id)
        )
    entity_type = _ENTITY_TYPES.get(entity.entity_type)
    if entity_type is None:
        raise ValueError("Unsupported Telegram entity type for publication.")
    if entity.entity_type == "pre":
        return entity_type(entity.offset_utf16, entity.length_utf16, "")
    if entity.entity_type == "text_url":
        raise ValueError("Text URL publication requires its safe URL metadata.")
    return entity_type(entity.offset_utf16, entity.length_utf16)


class TelethonPublisherGateway:
    """Publish through the authenticated Premium User API session only."""

    def __init__(self, client: TelethonPublisherClient, *, media_root: Path) -> None:
        """Store the client and canonical private media root."""
        self._client = client
        self._media_root = media_root.resolve(strict=True)

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        """Send destination-ready content with a bounded SDK operation."""
        if timeout_seconds <= 0:
            raise ValueError("Publisher timeout must be positive.")
        entities = [_map_entity(value) for value in payload.entities]
        try:
            async with asyncio.timeout(timeout_seconds):
                if not payload.media:
                    result = await self._client.send_message(
                        payload.destination_id,
                        payload.text or "",
                        formatting_entities=entities,
                        parse_mode=None,
                    )
                else:
                    paths = [
                        self._resolve_media(item.storage_path) for item in payload.media
                    ]
                    file_value: object = paths[0] if len(paths) == 1 else paths
                    result = await self._client.send_file(
                        payload.destination_id,
                        file_value,
                        caption=payload.text,
                        formatting_entities=entities,
                        parse_mode=None,
                    )
        except asyncio.CancelledError:
            raise
        except TimeoutError as error:
            raise PublisherError(
                PublicationFailureCategory.TIMEOUT,
                request_may_have_reached_telegram=True,
            ) from error
        except errors.FloodWaitError as error:
            raise PublisherError(
                PublicationFailureCategory.RATE_LIMIT,
                retry_after_seconds=float(error.seconds),
            ) from error
        except (errors.ChatAdminRequiredError, errors.ChannelPrivateError) as error:
            raise PublisherError(PublicationFailureCategory.PERMISSION) from error
        except (errors.RPCError, OSError) as error:
            raise PublisherError(
                PublicationFailureCategory.AMBIGUOUS,
                request_may_have_reached_telegram=True,
            ) from error
        values = result if isinstance(result, list | tuple) else (result,)
        message_ids = tuple(getattr(value, "id", 0) for value in values)
        if not message_ids or any(
            type(value) is not int or value <= 0 for value in message_ids
        ):
            raise PublisherError(
                PublicationFailureCategory.AMBIGUOUS,
                request_may_have_reached_telegram=True,
            )
        return PublishedMessage(message_ids, datetime.now(UTC))

    def _resolve_media(self, storage_path: str) -> str:
        candidate = Path(storage_path)
        if not candidate.is_absolute():
            candidate = self._media_root / candidate
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(self._media_root)
        except (OSError, ValueError):
            raise PublisherError(PublicationFailureCategory.PERMANENT) from None
        if not resolved.is_file():
            raise PublisherError(PublicationFailureCategory.PERMANENT)
        return str(resolved)


__all__ = ("TelethonPublisherClient", "TelethonPublisherGateway")
