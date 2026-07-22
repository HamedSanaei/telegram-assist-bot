"""Domain models for advertisement source identity, versioned snapshots, and hashing."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Final

from telegram_assist_bot.domain.media import MediaType

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain.posts import TelegramEntity

_SNAPSHOT_CONTRACT_VERSION: Final[str] = "1.0.0"


class AdvertisementSourceState(StrEnum):
    """Explicit lifecycle/availability states of an advertisement source post."""

    AVAILABLE = "available"
    UNCHANGED = "unchanged"
    REFRESHED = "refreshed"
    TEMPORARILY_UNAVAILABLE = "temporarily_unavailable"
    PERMANENTLY_UNAVAILABLE = "permanently_unavailable"
    SOURCE_DELETED = "source_deleted"
    STALE_FALLBACK_USED = "stale_fallback_used"


class FetchAdvertisementSourceOutcomeKind(StrEnum):
    """Explicit result kinds for advertisement source resolution use cases."""

    CACHE_HIT = "cache_hit"
    FETCHED_INITIAL = "fetched_initial"
    REFRESH_UNCHANGED = "refresh_unchanged"
    REFRESH_UPDATED = "refresh_updated"
    STALE_FALLBACK = "stale_fallback"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class AdvertisementSourceFetchPolicy:
    """Explicit bounded timeout and retry policy for Telegram source resolution."""

    timeout_seconds: int
    max_attempts: int
    initial_backoff_seconds: int

    def __post_init__(self) -> None:
        """Validate policy ranges without supplying application defaults."""
        if (
            type(self.timeout_seconds) is not int
            or not 1 <= self.timeout_seconds <= 120
        ):
            raise ValueError("timeout_seconds must be between 1 and 120")
        if type(self.max_attempts) is not int or not 1 <= self.max_attempts <= 10:
            raise ValueError("max_attempts must be between 1 and 10")
        if (
            type(self.initial_backoff_seconds) is not int
            or not 0 <= self.initial_backoff_seconds <= 300
        ):
            raise ValueError("initial_backoff_seconds must be between 0 and 300")


@dataclass(frozen=True, slots=True)
class AdvertisementMediaReference:
    """Reference one content-addressed cached advertisement media item."""

    media_type: MediaType
    item_index: int
    size_bytes: int
    mime_type: str | None
    original_filename: str | None
    storage_path: str
    media_group_id: str | None = None

    def __post_init__(self) -> None:
        """Validate storage-safe immutable media metadata."""
        if not isinstance(self.media_type, MediaType):
            raise ValueError("media_type must be a MediaType")
        if type(self.item_index) is not int or self.item_index < 0:
            raise ValueError("item_index must be a non-negative integer")
        if type(self.size_bytes) is not int or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")
        if not self.storage_path or self.storage_path.isspace():
            raise ValueError("storage_path must be non-blank")


@dataclass(frozen=True, slots=True)
class AdvertisementSourceIdentity:
    """Stable identity descriptor for an advertisement source post."""

    campaign_id: str
    source_channel_username: str
    source_message_id: int
    source_identity_fingerprint: str

    def __post_init__(self) -> None:
        """Validate source identity invariants."""
        if not self.campaign_id or self.campaign_id.isspace():
            raise ValueError("campaign_id must be non-blank")
        if not self.source_channel_username or self.source_channel_username.isspace():
            raise ValueError("source_channel_username must be non-blank")
        if type(self.source_message_id) is not int or self.source_message_id <= 0:
            raise ValueError("source_message_id must be a positive integer")
        if (
            not self.source_identity_fingerprint
            or self.source_identity_fingerprint.isspace()
        ):
            raise ValueError("source_identity_fingerprint must be non-blank")

    @classmethod
    def create(
        cls,
        campaign_id: str,
        source_channel_username: str,
        source_message_id: int,
    ) -> AdvertisementSourceIdentity:
        """Construct a stable fingerprint for a campaign source post."""
        clean_user = source_channel_username.strip().lower()
        raw_key = f"{campaign_id}:{clean_user}:{source_message_id}"
        fingerprint = hashlib.sha256(raw_key.encode("utf-8")).hexdigest()
        return cls(
            campaign_id=campaign_id,
            source_channel_username=source_channel_username,
            source_message_id=source_message_id,
            source_identity_fingerprint=fingerprint,
        )


@dataclass(frozen=True, slots=True)
class AdvertisementSourceSnapshot:
    """Immutable versioned snapshot of an advertisement source post's content."""

    snapshot_id: str
    campaign_id: str
    source_identity: AdvertisementSourceIdentity
    snapshot_version: int
    snapshot_contract_version: str
    content_hash: str
    text: str | None
    caption: str | None
    text_entities: tuple[TelegramEntity, ...]
    caption_entities: tuple[TelegramEntity, ...]
    media_group_id: str | None
    media_references: tuple[AdvertisementMediaReference, ...]
    source_published_at: datetime
    source_edited_at: datetime | None
    fetched_at: datetime
    last_successful_fetch_at: datetime
    is_current: bool = True
    expires_at: datetime | None = None
    is_stale: bool = False
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        """Validate invariant attributes of the snapshot."""
        if not self.snapshot_id or self.snapshot_id.isspace():
            raise ValueError("snapshot_id must be non-blank")
        if not self.campaign_id or self.campaign_id.isspace():
            raise ValueError("campaign_id must be non-blank")
        if not isinstance(self.source_identity, AdvertisementSourceIdentity):
            raise ValueError("source_identity must be an AdvertisementSourceIdentity")
        if type(self.snapshot_version) is not int or self.snapshot_version <= 0:
            raise ValueError("snapshot_version must be a positive integer")
        if not self.content_hash or self.content_hash.isspace():
            raise ValueError("content_hash must be non-blank")


def compute_canonical_content_hash(
    *,
    text: str | None,
    caption: str | None,
    text_entities: tuple[TelegramEntity, ...] = (),
    caption_entities: tuple[TelegramEntity, ...] = (),
    media_group_id: str | None = None,
    media_references: tuple[AdvertisementMediaReference, ...] = (),
    album_member_identities: tuple[int, ...] = (),
    contract_version: str = _SNAPSHOT_CONTRACT_VERSION,
) -> str:
    """Compute a deterministic content hash for an advertisement source payload."""
    payload = {
        "contract_version": contract_version,
        "text": text,
        "caption": caption,
        "text_entities": [
            {
                "offset_utf16": e.offset_utf16,
                "length_utf16": e.length_utf16,
                "entity_type": e.entity_type,
                "custom_emoji_id": e.custom_emoji_id,
                "url": e.url,
            }
            for e in text_entities
        ],
        "caption_entities": [
            {
                "offset_utf16": e.offset_utf16,
                "length_utf16": e.length_utf16,
                "entity_type": e.entity_type,
                "custom_emoji_id": e.custom_emoji_id,
                "url": e.url,
            }
            for e in caption_entities
        ],
        "media_group_id": media_group_id,
        "media_references": [
            {
                "media_type": str(m.media_type),
                "item_index": m.item_index,
                "size_bytes": m.size_bytes,
                "mime_type": m.mime_type,
                "storage_path": m.storage_path,
            }
            for m in media_references
        ],
        "album_member_identities": list(album_member_identities),
    }
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
