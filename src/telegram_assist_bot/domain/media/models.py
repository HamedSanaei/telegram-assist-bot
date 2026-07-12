"""Pure immutable media value objects."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datetime import datetime


class MediaType(StrEnum):
    """Enumerate supported Telegram media kinds."""

    PHOTO = "Photo"
    VIDEO = "Video"
    DOCUMENT = "Document"
    AUDIO = "Audio"
    VOICE = "Voice"
    ANIMATION = "Animation"
    STICKER = "Sticker"
    VIDEO_NOTE = "VideoNote"


@dataclass(frozen=True, slots=True)
class MediaIdentity:
    """Identify one media item independently from SDK file references."""

    source_channel_id: int
    source_message_id: int
    item_index: int = 0

    def __post_init__(self) -> None:
        """Validate source-scoped media identity values."""
        if (
            self.source_channel_id == 0
            or self.source_message_id <= 0
            or self.item_index < 0
        ):
            raise ValueError("Media identity values are invalid.")

    @property
    def key(self) -> str:
        """Return a deterministic storage-safe identity key."""
        return f"{self.source_channel_id}_{self.source_message_id}_{self.item_index}"


@dataclass(frozen=True, slots=True)
class StoredMedia:
    """Describe committed private media without exposing provider objects."""

    identity: MediaIdentity
    media_type: MediaType
    content_hash: str
    size_bytes: int
    mime_type: str | None
    original_filename: str | None
    storage_path: str
    expires_at: datetime

    def __post_init__(self) -> None:
        """Validate digest, size and timezone-aware expiration."""
        if len(self.content_hash) != 64 or self.size_bytes < 0:
            raise ValueError("Stored media metadata is invalid.")
        if self.expires_at.tzinfo is None or self.expires_at.utcoffset() is None:
            raise ValueError("Media expiration must be timezone-aware.")
