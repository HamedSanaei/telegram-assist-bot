"""Application-owned ports and DTOs for private administrator interaction."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain import (
        ApprovalReference,
        CallbackClaims,
        DestinationSelection,
    )
    from telegram_assist_bot.domain.posts import TelegramEntity


@dataclass(frozen=True, slots=True)
class BotUpdate:
    """Trusted identity extracted from a Bot API update boundary."""

    actor_id: int
    chat_id: int
    chat_type: str
    callback_data: str | None = None
    callback_query_id: str | None = None


@dataclass(frozen=True, slots=True)
class InlineButton:
    """SDK-independent inline button."""

    label: str
    callback_data: str


@dataclass(frozen=True, slots=True)
class InlineKeyboard:
    """SDK-independent inline keyboard."""

    rows: tuple[tuple[InlineButton, ...], ...]


@dataclass(frozen=True, slots=True)
class ApprovalMedia:
    """Describe one private approval-preview media item without an SDK type."""

    media_type: str
    storage_path: str
    mime_type: str | None = None
    original_filename: str | None = None

    def __post_init__(self) -> None:
        """Reject blank media identity and unsupported preview kinds."""
        if (
            self.media_type.lower()
            not in {
                "photo",
                "video",
                "animation",
                "document",
            }
            or not self.storage_path
            or self.storage_path.isspace()
        ):
            raise ValueError("Approval media metadata is invalid.")


@dataclass(frozen=True, slots=True)
class ApprovalContent:
    """Prepared content sent separately from the managerial header."""

    text: str | None
    caption: str | None
    text_entities: tuple[TelegramEntity, ...] = ()
    caption_entities: tuple[TelegramEntity, ...] = ()
    media_paths: tuple[str, ...] = ()
    media: tuple[ApprovalMedia, ...] = ()


class BotEditOutcome(StrEnum):
    """Stable result of an approval header edit."""

    UPDATED = "updated"
    NOT_MODIFIED = "not_modified"
    DELETED = "deleted"


class ApprovalDeliveryError(RuntimeError):
    """Represent a safe Bot API approval-delivery failure."""

    error_category = "delivery_error"


class ApprovalDeliveryUnavailableError(ApprovalDeliveryError):
    """Report that an administrator cannot currently receive a Bot message."""

    error_category = "delivery_unavailable"


class ApprovalDeliveryRateLimitError(ApprovalDeliveryError):
    """Report one bounded Bot API retry delay without SDK details."""

    error_category = "rate_limited"

    def __init__(self, retry_after_seconds: int) -> None:
        """Retain only a non-negative provider-supplied retry delay."""
        self.retry_after_seconds = max(0, retry_after_seconds)
        super().__init__("Approval delivery is rate limited.")


class ApprovalDeliveryTransientError(ApprovalDeliveryError):
    """Report a retryable transport failure at the Bot API boundary."""

    error_category = "transient"


class ApprovalDeliveryRejectedError(ApprovalDeliveryError):
    """Report a safe retryable Bot API request rejection."""

    error_category = "bad_request"


class ApprovalMediaPathError(ApprovalDeliveryRejectedError):
    """Report a missing or unsafe private approval media path."""

    error_category = "invalid_media_path"


class ApprovalMediaUploadTimeoutError(ApprovalDeliveryTransientError):
    """Report that a bounded approval media upload timed out."""

    error_category = "media_upload_timeout"


class ApprovalMediaNetworkError(ApprovalDeliveryTransientError):
    """Report a retryable network failure specific to media upload."""

    error_category = "media_network"


class ApprovalMediaRejectedError(ApprovalDeliveryRejectedError):
    """Report a permanent Bot API rejection of prepared approval media."""

    error_category = "media_rejected"


class AdminMessagingGateway(Protocol):
    """Hide Bot SDK requests and exceptions from Application."""

    async def send_header(
        self,
        chat_id: int,
        text: str,
        keyboard: InlineKeyboard | None = None,
        *,
        reply_to_message_id: int | None = None,
    ) -> int:
        """Send one canonical managerial header."""
        ...

    async def send_content(
        self, chat_id: int, content: ApprovalContent
    ) -> tuple[int, ...]:
        """Send prepared content separately and return identifiers."""
        ...

    async def edit_header(
        self, chat_id: int, message_id: int, text: str, keyboard: InlineKeyboard
    ) -> BotEditOutcome:
        """Edit the canonical header and keyboard."""
        ...

    async def answer_callback(self, query_id: str, text: str, *, alert: bool) -> None:
        """Answer a callback with safe user-visible text."""
        ...

    async def close(self) -> None:
        """Close the owned Bot resource idempotently."""
        ...


class ApprovalRepository(Protocol):
    """Persist callback, approval, selection, and synchronization state."""

    async def insert_callback(self, claims: CallbackClaims) -> None:
        """Persist server-only callback claims."""
        ...

    async def get_callback(self, digest: str) -> CallbackClaims | None:
        """Load claims by opaque token digest."""
        ...

    async def revoke_post_callbacks(self, post_id: str) -> int:
        """Revoke outstanding callbacks for one Post."""
        ...

    async def save_reference(self, reference: ApprovalReference) -> ApprovalReference:
        """Persist an identifiable successful delivery idempotently."""
        ...

    async def get_reference(self, reference_id: str) -> ApprovalReference | None:
        """Load delivery progress by stable identity."""
        ...

    async def save_delivery_progress(
        self, reference: ApprovalReference
    ) -> ApprovalReference:
        """Persist an inactive identifiable header before content delivery."""
        ...

    async def complete_reference(
        self, reference_id: str, control_message_id: int
    ) -> ApprovalReference:
        """Atomically activate a reference after control-card success."""
        ...

    async def list_active_references(
        self, post_id: str
    ) -> tuple[ApprovalReference, ...]:
        """List active private approval references."""
        ...

    async def get_selection(
        self, post_id: str, destination_id: int
    ) -> DestinationSelection:
        """Load a selection with legacy-none defaults."""
        ...

    async def compare_and_set_selection(
        self, current: DestinationSelection, updated: DestinationSelection
    ) -> bool:
        """Atomically compare and set one selection."""
        ...

    async def mark_sync_success(self, reference_id: str, version: int) -> bool:
        """Advance one rendered version unless stale."""
        ...

    async def mark_sync_failure(
        self,
        reference_id: str,
        version: int,
        *,
        category: str,
        next_retry_at: datetime | None,
        inactive: bool,
    ) -> bool:
        """Persist safe bounded retry or permanent inactive state."""
        ...

    async def claim_retry(
        self, reference_id: str, *, now: datetime, lease_until: datetime
    ) -> bool:
        """Atomically claim one due retry lease."""
        ...


__all__ = (
    "AdminMessagingGateway",
    "ApprovalContent",
    "ApprovalDeliveryError",
    "ApprovalDeliveryRateLimitError",
    "ApprovalDeliveryRejectedError",
    "ApprovalDeliveryTransientError",
    "ApprovalDeliveryUnavailableError",
    "ApprovalMedia",
    "ApprovalMediaNetworkError",
    "ApprovalMediaPathError",
    "ApprovalMediaRejectedError",
    "ApprovalMediaUploadTimeoutError",
    "ApprovalRepository",
    "BotEditOutcome",
    "BotUpdate",
    "InlineButton",
    "InlineKeyboard",
)
