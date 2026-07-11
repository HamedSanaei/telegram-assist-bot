"""Map Telethon message objects to exact application-owned text DTOs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final

from telegram_assist_bot.application.ports import TelegramTextMessage
from telegram_assist_bot.domain.posts import TelegramEntity

_CAMEL_BOUNDARY: Final[re.Pattern[str]] = re.compile(r"(?<!^)(?=[A-Z])")


class InvalidTelegramMessageError(ValueError):
    """Report an SDK message that cannot satisfy the ingestion contract."""


def _entity_type(raw_entity: object) -> str:
    name = type(raw_entity).__name__
    prefix = "MessageEntity"
    value = name[len(prefix) :] if name.startswith(prefix) else name
    normalized = _CAMEL_BOUNDARY.sub("_", value).lower()
    if not normalized:
        raise InvalidTelegramMessageError
    return normalized


def _map_entity(raw_entity: object) -> TelegramEntity:
    offset = getattr(raw_entity, "offset", None)
    length = getattr(raw_entity, "length", None)
    if type(offset) is not int or type(length) is not int:
        raise InvalidTelegramMessageError
    entity_type = _entity_type(raw_entity)
    custom_emoji_id: str | None = None
    if entity_type == "custom_emoji":
        document_id = getattr(raw_entity, "document_id", None)
        if type(document_id) is not int or document_id <= 0:
            raise InvalidTelegramMessageError
        custom_emoji_id = str(document_id)
    try:
        return TelegramEntity(
            offset_utf16=offset,
            length_utf16=length,
            entity_type=entity_type,
            custom_emoji_id=custom_emoji_id,
        )
    except Exception as error:
        raise InvalidTelegramMessageError from error


def map_telethon_message(
    raw_message: object,
    *,
    source_channel_id: int,
    source_channel_username: str | None,
    source_channel_display_name: str,
) -> TelegramTextMessage:
    """Map one SDK object without normalizing text, entities, or timestamps."""
    message_id = getattr(raw_message, "id", None)
    published_at = getattr(raw_message, "date", None)
    if type(message_id) is not int or message_id <= 0:
        raise InvalidTelegramMessageError
    if type(published_at) is not datetime or published_at.tzinfo is None:
        raise InvalidTelegramMessageError
    try:
        published_at = published_at.astimezone(UTC)
    except (OverflowError, ValueError):
        raise InvalidTelegramMessageError from None

    raw_text = getattr(raw_message, "message", None)
    if raw_text is not None and type(raw_text) is not str:
        raise InvalidTelegramMessageError
    raw_entities = getattr(raw_message, "entities", None) or ()
    if not isinstance(raw_entities, list | tuple):
        raise InvalidTelegramMessageError
    entities = tuple(_map_entity(entity) for entity in raw_entities)
    has_media = getattr(raw_message, "media", None) is not None
    is_service = (
        type(raw_message).__name__ == "MessageService"
        or getattr(raw_message, "action", None) is not None
    )
    return TelegramTextMessage(
        source_channel_id=source_channel_id,
        source_channel_username=source_channel_username,
        source_channel_display_name=source_channel_display_name,
        source_message_id=message_id,
        text=None if has_media else raw_text,
        caption=raw_text if has_media else None,
        text_entities=() if has_media else entities,
        caption_entities=entities if has_media else (),
        source_published_at=published_at,
        is_service=is_service,
        has_media=has_media,
    )


__all__ = ("InvalidTelegramMessageError", "map_telethon_message")
