"""Map Telethon message objects to exact application-owned text DTOs."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final

from telegram_assist_bot.application.ports import (
    TelegramMediaReference,
    TelegramTextMessage,
)
from telegram_assist_bot.domain.media import MediaType
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
    raw_media = getattr(raw_message, "media", None)
    has_media = raw_media is not None
    media: tuple[TelegramMediaReference, ...] = ()
    if has_media:
        document = getattr(raw_message, "document", None)
        photo = getattr(raw_message, "photo", None)
        if document is not None:
            mime_type = getattr(document, "mime_type", None)
            media_type = _document_media_type(
                mime_type, getattr(document, "attributes", ())
            )
            size = getattr(document, "size", None)
            filename = _document_filename(getattr(document, "attributes", ()))
        elif photo is not None:
            media_type, mime_type, filename = MediaType.PHOTO, "image/jpeg", None
            sizes = getattr(photo, "sizes", ())
            size = max((getattr(item, "size", 0) for item in sizes), default=None)
        else:
            media_type, mime_type, filename, size = MediaType.DOCUMENT, None, None, None
        grouped_id = getattr(raw_message, "grouped_id", None)
        media = (
            TelegramMediaReference(
                media_type,
                0,
                size if type(size) is int and size >= 0 else None,
                mime_type if type(mime_type) is str else None,
                filename,
                f"{source_channel_id}:{message_id}:0",
                str(grouped_id) if grouped_id is not None else None,
            ),
        )
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
        media=media,
    )


def _document_filename(attributes: object) -> str | None:
    for attribute in attributes if isinstance(attributes, list | tuple) else ():
        value = getattr(attribute, "file_name", None)
        if type(value) is str:
            return value
    return None


def _document_media_type(mime_type: object, attributes: object) -> MediaType:
    items = attributes if isinstance(attributes, list | tuple) else ()
    names = {type(item).__name__ for item in items}
    if "DocumentAttributeSticker" in names:
        return MediaType.STICKER
    if "DocumentAttributeVideo" in names:
        return (
            MediaType.ANIMATION
            if "DocumentAttributeAnimated" in names
            else MediaType.VIDEO
        )
    if "DocumentAttributeAudio" in names:
        return (
            MediaType.VOICE
            if any(getattr(item, "voice", False) for item in items)
            else MediaType.AUDIO
        )
    if mime_type == "video/mp4" and "DocumentAttributeAnimated" in names:
        return MediaType.ANIMATION
    return MediaType.DOCUMENT


__all__ = ("InvalidTelegramMessageError", "map_telethon_message")
