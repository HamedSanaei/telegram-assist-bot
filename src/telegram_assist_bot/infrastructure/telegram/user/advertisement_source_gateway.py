"""Telethon implementation of TelegramAdvertisementSourceGateway."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final, Protocol

from telethon.errors import (  # type: ignore[import-untyped]
    ChannelPrivateError,
    FloodWaitError,
    MessageIdInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
)

from telegram_assist_bot.application.ports import (
    AdvertisementSourceGroupDTO,
    AdvertisementSourceMessageDTO,
    AdvertisementSourceNotFoundError,
    AdvertisementSourcePermissionError,
    AdvertisementSourceTransientError,
    TelegramMediaReference,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramEntity

_CAMEL_BOUNDARY: Final[re.Pattern[str]] = re.compile(r"(?<!^)(?=[A-Z])")
_DOWNLOADABLE_MEDIA_TYPES: Final[frozenset[str]] = frozenset(
    {"MessageMediaDocument", "MessageMediaPhoto"}
)


class TelethonClientProtocol(Protocol):
    """Describe Telethon calls needed by the advertisement source gateway."""

    async def get_entity(self, entity: object) -> object:
        """Resolve a channel entity."""
        ...

    async def get_messages(
        self,
        entity: object,
        *,
        ids: int | list[int] | None = None,
        min_id: int | None = None,
        max_id: int | None = None,
        limit: int | None = None,
    ) -> object:
        """Fetch message(s) from a channel entity."""
        ...


def _entity_type(raw_entity: object) -> str:
    name = type(raw_entity).__name__
    prefix = "MessageEntity"
    value = name[len(prefix) :] if name.startswith(prefix) else name
    normalized = _CAMEL_BOUNDARY.sub("_", value).lower()
    return normalized or "unknown"


def _map_entity(raw_entity: object) -> TelegramEntity | None:
    offset = getattr(raw_entity, "offset", None)
    length = getattr(raw_entity, "length", None)
    if type(offset) is not int or type(length) is not int:
        return None
    entity_type = _entity_type(raw_entity)
    custom_emoji_id: str | None = None
    url: str | None = None
    if entity_type == "custom_emoji":
        doc_id = getattr(raw_entity, "document_id", None)
        if type(doc_id) is int and doc_id > 0:
            custom_emoji_id = str(doc_id)
    elif entity_type == "text_url":
        raw_url = getattr(raw_entity, "url", None)
        if type(raw_url) is str:
            url = raw_url
    try:
        return TelegramEntity(
            offset_utf16=offset,
            length_utf16=length,
            entity_type=entity_type,
            custom_emoji_id=custom_emoji_id,
            url=url,
        )
    except ValueError:
        return None


def _map_telethon_msg_to_dto(
    raw_message: object,
    channel_username: str,
    channel_id: int,
) -> AdvertisementSourceMessageDTO | None:
    msg_id = getattr(raw_message, "id", None)
    if type(msg_id) is not int or msg_id <= 0:
        return None
    raw_date = getattr(raw_message, "date", None)
    if not isinstance(raw_date, datetime):
        return None
    published_at = raw_date.astimezone(UTC)

    raw_edit = getattr(raw_message, "edit_date", None)
    edited_at: datetime | None = None
    if isinstance(raw_edit, datetime):
        edited_at = raw_edit.astimezone(UTC)

    raw_text = getattr(raw_message, "message", None)
    text: str | None = raw_text if isinstance(raw_text, str) and raw_text else None
    caption: str | None = None

    raw_entities = getattr(raw_message, "entities", None) or ()
    mapped_entities = tuple(
        ent for raw in raw_entities if (ent := _map_entity(raw)) is not None
    )

    grouped_id_val = getattr(raw_message, "grouped_id", None)
    media_group_id: str | None = str(grouped_id_val) if grouped_id_val else None

    raw_media = getattr(raw_message, "media", None)
    raw_media_type = type(raw_media).__name__
    has_media = raw_media_type in _DOWNLOADABLE_MEDIA_TYPES
    media_refs: tuple[TelegramMediaReference, ...] = ()

    if has_media:
        if raw_media_type == "MessageMediaDocument":
            doc = getattr(raw_message, "document", None)
            mime_type = getattr(doc, "mime_type", None) if doc else None
            size_bytes = getattr(doc, "size_bytes", None) if doc else None
            fname: str | None = None
            if doc and hasattr(doc, "attributes"):
                for attr in getattr(doc, "attributes", ()):
                    if type(attr).__name__ == "DocumentAttributeFilename":
                        fname = getattr(attr, "file_name", None)
            ref = f"{channel_id}:{msg_id}:0"
            media_refs = (
                TelegramMediaReference(
                    media_type=MediaType.DOCUMENT,
                    item_index=0,
                    size_bytes=size_bytes if isinstance(size_bytes, int) else None,
                    mime_type=mime_type if isinstance(mime_type, str) else None,
                    original_filename=fname if isinstance(fname, str) else None,
                    opaque_reference=ref,
                    media_group_id=media_group_id,
                ),
            )
            caption = text
            text = None
        elif raw_media_type == "MessageMediaPhoto":
            ref = f"{channel_id}:{msg_id}:0"
            media_refs = (
                TelegramMediaReference(
                    media_type=MediaType.PHOTO,
                    item_index=0,
                    size_bytes=None,
                    mime_type="image/jpeg",
                    original_filename=None,
                    opaque_reference=ref,
                    media_group_id=media_group_id,
                ),
            )
            caption = text
            text = None

    text_entities = () if caption is not None else mapped_entities
    caption_entities = mapped_entities if caption is not None else ()

    return AdvertisementSourceMessageDTO(
        source_channel_username=channel_username,
        source_message_id=msg_id,
        media_group_id=media_group_id,
        source_published_at=published_at,
        source_edited_at=edited_at,
        text=text,
        caption=caption,
        text_entities=text_entities,
        caption_entities=caption_entities,
        media=media_refs,
    )


class TelethonAdvertisementSourceGateway:
    """Telethon adapter implementing TelegramAdvertisementSourceGateway."""

    def __init__(self, client: TelethonClientProtocol) -> None:
        """Initialize with an active Telethon client."""
        self._client = client

    async def fetch_advertisement_post(
        self,
        channel_username: str,
        message_id: int,
    ) -> AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO:
        """Fetch a post or media group without exposing Telethon types."""
        clean_username = channel_username.strip().lstrip("@")
        try:
            entity = await self._client.get_entity(clean_username)
        except (UsernameNotOccupiedError, UsernameInvalidError) as err:
            raise AdvertisementSourceNotFoundError(
                f"Channel '@{clean_username}' was not found."
            ) from err
        except ChannelPrivateError as err:
            raise AdvertisementSourcePermissionError(
                f"Access to channel '@{clean_username}' is private or restricted."
            ) from err
        except FloodWaitError as err:
            raise AdvertisementSourceTransientError(
                "Telegram rate limit reached while resolving channel."
            ) from err
        except Exception as err:
            raise AdvertisementSourceTransientError(
                "Failed to resolve Telegram source channel."
            ) from err

        channel_id = getattr(entity, "id", 0)

        try:
            raw_msg = await self._client.get_messages(entity, ids=message_id)
        except MessageIdInvalidError as err:
            raise AdvertisementSourceNotFoundError(
                f"Message {message_id} was not found."
            ) from err
        except FloodWaitError as err:
            raise AdvertisementSourceTransientError(
                "Telegram rate limit reached while fetching message."
            ) from err
        except Exception as err:
            raise AdvertisementSourceTransientError(
                "Failed to fetch Telegram source message."
            ) from err

        if raw_msg is None or getattr(raw_msg, "empty", False):
            raise AdvertisementSourceNotFoundError(
                f"Message {message_id} in channel '@{clean_username}' "
                "does not exist or was deleted."
            )

        main_dto = _map_telethon_msg_to_dto(raw_msg, clean_username, channel_id)
        if main_dto is None:
            raise AdvertisementSourceNotFoundError(
                "Failed to map Telegram source message."
            )

        grouped_id = getattr(raw_msg, "grouped_id", None)
        if grouped_id is None:
            return main_dto

        # Fetch media group members
        try:
            min_id = max(1, message_id - 20)
            max_id = message_id + 20
            surrounding = await self._client.get_messages(
                entity,
                min_id=min_id,
                max_id=max_id,
                limit=50,
            )
        except Exception as err:
            raise AdvertisementSourceTransientError(
                "Failed to resolve media group members."
            ) from err

        raw_list = surrounding if isinstance(surrounding, list) else [raw_msg]
        group_members_raw = [
            m for m in raw_list if getattr(m, "grouped_id", None) == grouped_id
        ]
        if not any(getattr(m, "id", None) == message_id for m in group_members_raw):
            group_members_raw.append(raw_msg)

        group_members_raw.sort(key=lambda m: getattr(m, "id", 0))

        mapped_members: list[AdvertisementSourceMessageDTO] = []
        for raw_m in group_members_raw:
            dto = _map_telethon_msg_to_dto(raw_m, clean_username, channel_id)
            if dto is not None:
                mapped_members.append(dto)

        if not mapped_members:
            return main_dto

        # Use the first member carrying a caption as the canonical album caption.
        canonical_caption: str | None = None
        canonical_caption_entities: tuple[TelegramEntity, ...] = ()
        for member in mapped_members:
            if member.caption is not None:
                canonical_caption = member.caption
                canonical_caption_entities = member.caption_entities
                break

        return AdvertisementSourceGroupDTO(
            media_group_id=str(grouped_id),
            members=tuple(mapped_members),
            canonical_caption=canonical_caption,
            canonical_caption_entities=canonical_caption_entities,
        )
