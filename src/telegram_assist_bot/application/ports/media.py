"""Application-owned ports for private media and preparation persistence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from telegram_assist_bot.domain.categories import CategorizationResult
    from telegram_assist_bot.domain.duplicates import DuplicateCheckResult
    from telegram_assist_bot.domain.media import MediaIdentity, MediaType, StoredMedia
    from telegram_assist_bot.domain.posts import PostId, TelegramEntity


class MediaOperationError(Exception):
    """Base safe media-boundary failure."""


class MediaTransientError(MediaOperationError):
    """Report a retryable media operation failure."""

    error_category = "transient"


class MediaRateLimitError(MediaTransientError):
    """Report a retryable provider wait without exposing provider details."""

    error_category = "rate_limit"

    def __init__(self, retry_after_seconds: int) -> None:
        """Retain only one bounded non-negative delay."""
        if type(retry_after_seconds) is not int or retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be a non-negative integer")
        self.retry_after_seconds = retry_after_seconds
        super().__init__("Media provider rate limit was reached.")


class MediaPermanentError(MediaOperationError):
    """Report a non-retryable media operation failure."""

    error_category = "permanent"


class MediaTooLargeError(MediaPermanentError):
    """Report a stream exceeding its configured maximum size."""


class MediaSource(Protocol):
    """Provide provider-neutral media byte streams."""

    async def open(self, opaque_reference: str) -> AsyncIterator[bytes]:
        """Open a fresh stream without exposing provider objects."""
        ...


class MediaStorage(Protocol):
    """Store and delete files confined to a private runtime root."""

    async def store(
        self,
        identity: MediaIdentity,
        stream: AsyncIterator[bytes],
        *,
        maximum_bytes: int,
    ) -> tuple[str, int, str]:
        """Commit a stream atomically and return path, size and digest."""
        ...

    async def exists(self, storage_path: str) -> bool:
        """Return whether a confined committed file exists."""
        ...

    async def delete(self, storage_path: str) -> bool:
        """Delete a confined file idempotently."""
        ...

    async def delete_stale_temporary_files(
        self, *, older_than: datetime, limit: int
    ) -> int:
        """Delete a bounded set of stale owned temporary files."""
        ...


@dataclass(frozen=True, slots=True)
class MediaDownloadSpec:
    """Describe one provider-neutral media download."""

    identity: MediaIdentity
    media_type: MediaType
    opaque_reference: str
    mime_type: str | None
    original_filename: str | None
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class MediaGroupMember:
    """Persist one immutable member of a Telegram media group."""

    source_message_id: int
    source_date: datetime
    media: StoredMedia
    caption: str | None = None
    caption_entities: tuple[TelegramEntity, ...] = ()


@dataclass(frozen=True, slots=True)
class MediaGroup:
    """Represent durable group assembly state."""

    group_key: str
    source_channel_id: int
    telegram_group_id: str
    members: tuple[MediaGroupMember, ...]
    first_member_at: datetime
    last_member_at: datetime
    finalize_after: datetime
    maximum_wait_until: datetime
    finalized_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class DestinationArtifact:
    """Persist one canonical prepared artifact per post and destination."""

    post_id: PostId
    destination_id: str
    text: str | None
    entities: tuple[TelegramEntity, ...]
    content_policy_version: int


class ContentPreparationRepository(Protocol):
    """Persist media and preparation state with atomic conditional operations."""

    async def get_media(self, identity: MediaIdentity) -> StoredMedia | None:
        """Load one canonical media record."""
        ...

    async def save_media_if_absent(self, media: StoredMedia) -> StoredMedia:
        """Insert or return one canonical media record."""
        ...

    async def list_media_for_preview(self) -> tuple[StoredMedia, ...]:
        """List non-cleaned media records for an idempotent preview backfill."""
        ...

    async def list_cleanup_candidates(
        self, *, now: datetime, orphan_before: datetime, limit: int
    ) -> tuple[StoredMedia, ...]:
        """List a bounded cleanup candidate batch."""
        ...

    async def is_storage_path_referenced(
        self, storage_path: str, *, now: datetime
    ) -> bool:
        """Recheck live references to one storage path."""
        ...

    async def mark_media_cleaned(
        self, identity: MediaIdentity, *, cleaned_at: datetime
    ) -> bool:
        """Conditionally mark one media record cleaned."""
        ...

    async def add_group_member(
        self, group: MediaGroup, member: MediaGroupMember
    ) -> MediaGroup:
        """Append one replay-safe group member."""
        ...

    async def get_group(self, group_key: str) -> MediaGroup | None:
        """Load one durable media group."""
        ...

    async def finalize_group(self, group_key: str, *, at: datetime) -> bool:
        """Atomically finalize one due group."""
        ...

    async def list_due_groups(
        self, *, now: datetime, limit: int
    ) -> tuple[MediaGroup, ...]:
        """List a bounded deterministic batch awaiting finalization."""
        ...

    async def find_duplicate(
        self, *, content_hash: str, post_id: PostId, since: datetime
    ) -> PostId | None:
        """Find a prior exact hash match inside a bounded window."""
        ...

    async def save_duplicate_result(
        self, post_id: PostId, result: DuplicateCheckResult
    ) -> DuplicateCheckResult:
        """Persist or return the canonical exact-duplicate result."""
        ...

    async def get_duplicate_result(
        self, post_id: PostId
    ) -> DuplicateCheckResult | None:
        """Load a previously completed exact-duplicate stage."""
        ...

    async def save_category_result(
        self, post_id: PostId, result: CategorizationResult
    ) -> CategorizationResult:
        """Persist category without overwriting a manual assignment."""
        ...

    async def get_category_result(self, post_id: PostId) -> CategorizationResult | None:
        """Load a previously completed category stage."""
        ...

    async def save_destination_artifact(
        self, artifact: DestinationArtifact
    ) -> DestinationArtifact:
        """Persist one canonical destination artifact."""
        ...

    async def get_destination_artifact(
        self, post_id: PostId, destination_id: str
    ) -> DestinationArtifact | None:
        """Load a previously completed destination stage."""
        ...

    async def mark_preparation_ready(self, post_id: PostId, *, at: datetime) -> bool:
        """Atomically create the final readiness marker once."""
        ...
