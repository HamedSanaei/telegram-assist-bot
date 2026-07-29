"""Telethon User API adapter for destination text, media, and albums."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol, cast

from telethon import errors, types  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import PublisherError
from telegram_assist_bot.domain import PublicationFailureCategory, PublishedMessage
from telegram_assist_bot.infrastructure.telegram.media_serializer import (
    TelethonMediaSerializationError,
    TelethonMediaSerializer,
)
from telegram_assist_bot.infrastructure.telegram.url_button_fallback import (
    materialize_url_buttons,
)
from telegram_assist_bot.shared.config import LogLevel

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from telegram_assist_bot.application.ports import PublicationPayload
    from telegram_assist_bot.domain.posts import TelegramEntity
    from telegram_assist_bot.shared.observability import StructuredLogger


class TelethonPublisherClient(Protocol):
    """Describe only SDK calls required by destination publication."""

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        """Send one text message."""
        ...

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        """Send one media item or album."""
        ...

    async def upload_file(self, file: object, **kwargs: object) -> object:
        """Upload one media item with explicit filename metadata."""
        ...


class TelethonPublisherDeletionClient(Protocol):
    """Describe the extra User API call required for receipt deletion."""

    async def delete_messages(
        self, entity: int, message_ids: tuple[int, ...], *, revoke: bool
    ) -> object:
        """Delete exact destination messages through the same User session."""
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
        if entity.url is None:
            raise ValueError("Text URL publication metadata is missing.")
        return entity_type(entity.offset_utf16, entity.length_utf16, entity.url)
    return entity_type(entity.offset_utf16, entity.length_utf16)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _prepare_entities(
    text: str | None,
    entities: tuple[TelegramEntity, ...],
) -> tuple[list[object], int]:
    """Validate source bounds and omit only legacy text URLs missing metadata."""
    if entities and text is None:
        raise ValueError("Publication entities require visible text.")
    text_length = _utf16_length(text or "")
    mapped: list[object] = []
    omitted = 0
    for entity in entities:
        if entity.entity_type == "text_url" and entity.url is None:
            omitted += 1
            continue
        if entity.offset_utf16 + entity.length_utf16 > text_length:
            raise ValueError("Publication entity bounds are outside visible text.")
        mapped.append(_map_entity(entity))
    return mapped, omitted


class TelethonPublisherGateway:
    """Publish through the authenticated Premium User API session only."""

    def __init__(
        self,
        client: TelethonPublisherClient,
        *,
        media_root: Path,
        logger: StructuredLogger | None = None,
    ) -> None:
        """Store the client and canonical private media root."""
        self._client = client
        self._media = TelethonMediaSerializer(client, media_root=media_root)
        self._logger = logger

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        """Send destination-ready content with a bounded SDK operation."""
        try:
            if timeout_seconds <= 0:
                raise ValueError("Publisher timeout must be positive.")
            destination_value: object = payload.destination_id
            if (
                not isinstance(destination_value, int)
                or isinstance(destination_value, bool)
                or destination_value == 0
            ):
                raise ValueError("Publisher destination is invalid.")
            destination_id = int(destination_value)
            content = materialize_url_buttons(
                payload.text,
                payload.entities,
                payload.inline_keyboard,
                has_media=bool(payload.media),
            )
            entities, omitted_text_urls = _prepare_entities(
                content.text, content.entities
            )
        except (AttributeError, OverflowError, TypeError, ValueError) as error:
            raise PublisherError(
                PublicationFailureCategory.PERMANENT,
                request_may_have_reached_telegram=False,
                reason_code="invalid_publication_payload",
            ) from error
        if omitted_text_urls and self._logger is not None:
            self._logger.emit(
                level=LogLevel.WARNING,
                event_name="publication_entity_omitted",
                fields={
                    "target_destination_id": payload.destination_id,
                    "entity_kind": "text_url",
                    "omission_reason": "missing_url_metadata",
                    "omitted_count": omitted_text_urls,
                },
            )
        try:
            async with asyncio.timeout(timeout_seconds):
                if not payload.media:
                    kwargs: dict[str, object] = {
                        "formatting_entities": entities,
                        "parse_mode": None,
                    }
                    result = await self._client.send_message(
                        destination_id,
                        content.text or "",
                        **kwargs,
                    )
                else:
                    serialized = await self._media.serialize(payload.media)
                    file_value: object = (
                        serialized[0] if len(serialized) == 1 else list(serialized)
                    )
                    kwargs = {
                        "caption": content.text,
                        "formatting_entities": entities,
                        "parse_mode": None,
                    }
                    result = await self._client.send_file(
                        destination_id, file_value, **kwargs
                    )
        except asyncio.CancelledError:
            raise
        except TelethonMediaSerializationError as error:
            raise PublisherError(
                PublicationFailureCategory.PERMANENT,
                request_may_have_reached_telegram=False,
                reason_code="media_serialization_failed",
            ) from error
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

    async def delete(
        self,
        destination_id: int,
        message_ids: tuple[int, ...],
        *,
        timeout_seconds: float,
    ) -> None:
        """Delete only validated receipt IDs from their persisted destination."""
        try:
            if (
                timeout_seconds <= 0
                or type(destination_id) is not int
                or destination_id == 0
                or not message_ids
                or any(type(value) is not int or value <= 0 for value in message_ids)
            ):
                raise ValueError("Publication retraction input is invalid.")
            async with asyncio.timeout(timeout_seconds):
                await cast(
                    "TelethonPublisherDeletionClient", self._client
                ).delete_messages(destination_id, message_ids, revoke=True)
        except asyncio.CancelledError:
            raise
        except ValueError as error:
            raise PublisherError(
                PublicationFailureCategory.PERMANENT,
                reason_code="invalid_retraction_receipt",
            ) from error
        except TimeoutError as error:
            raise PublisherError(
                PublicationFailureCategory.TIMEOUT,
                request_may_have_reached_telegram=True,
            ) from error
        except errors.FloodWaitError as error:
            raise PublisherError(
                PublicationFailureCategory.RATE_LIMIT,
                request_may_have_reached_telegram=True,
                retry_after_seconds=float(error.seconds),
            ) from error
        except (errors.ChatAdminRequiredError, errors.ChannelPrivateError) as error:
            raise PublisherError(PublicationFailureCategory.PERMISSION) from error
        except (errors.RPCError, OSError) as error:
            raise PublisherError(
                PublicationFailureCategory.AMBIGUOUS,
                request_may_have_reached_telegram=True,
            ) from error


__all__ = ("TelethonPublisherClient", "TelethonPublisherGateway")
