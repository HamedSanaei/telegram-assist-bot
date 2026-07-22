"""Application ports and DTOs for Telegram advertisement source resolution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports import TelegramMediaReference
    from telegram_assist_bot.domain.posts import TelegramEntity


class AdvertisementSourceError(Exception):
    """Base boundary exception for advertisement source post resolution."""


class AdvertisementSourceNotFoundError(AdvertisementSourceError):
    """Report a source channel or message that does not exist or was deleted."""


class AdvertisementSourcePermissionError(AdvertisementSourceError):
    """Report insufficient permissions to read the source channel or message."""


class AdvertisementSourceTransientError(AdvertisementSourceError):
    """Report a temporary network or Telegram API failure."""


@dataclass(frozen=True, slots=True)
class AdvertisementSourceMessageDTO:
    """SDK-independent DTO for a single advertisement source message."""

    source_channel_username: str
    source_message_id: int
    media_group_id: str | None
    source_published_at: datetime
    source_edited_at: datetime | None
    text: str | None
    caption: str | None
    text_entities: tuple[TelegramEntity, ...] = ()
    caption_entities: tuple[TelegramEntity, ...] = ()
    media: tuple[TelegramMediaReference, ...] = ()


@dataclass(frozen=True, slots=True)
class AdvertisementSourceGroupDTO:
    """SDK-independent DTO for an entire Media Group / Album advertisement source."""

    media_group_id: str
    members: tuple[AdvertisementSourceMessageDTO, ...]
    canonical_caption: str | None
    canonical_caption_entities: tuple[TelegramEntity, ...] = ()

    def __post_init__(self) -> None:
        """Enforce album DTO invariants."""
        if not self.media_group_id or self.media_group_id.isspace():
            raise ValueError("media_group_id must be non-blank")
        if not self.members:
            raise ValueError("album members tuple must not be empty")


@runtime_checkable
class TelegramAdvertisementSourceGateway(Protocol):
    """Port for fetching advertisement source posts without Telethon SDK leaks."""

    async def fetch_advertisement_post(
        self,
        channel_username: str,
        message_id: int,
    ) -> AdvertisementSourceGroupDTO | AdvertisementSourceMessageDTO:
        """Fetch one post or complete media group by public source identity."""
        ...
