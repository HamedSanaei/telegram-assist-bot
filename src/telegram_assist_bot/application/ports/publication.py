"""Application-owned publication boundaries and transport-neutral payloads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.domain import (
        Publication,
        PublicationFailureCategory,
        PublishedMessage,
    )
    from telegram_assist_bot.domain.media import MediaType
    from telegram_assist_bot.domain.posts import TelegramEntity


@dataclass(frozen=True, slots=True)
class PublicationMedia:
    """Describe one already-validated private media item for publication."""

    media_type: MediaType
    storage_path: str
    expires_at: datetime
    ready: bool = True
    mime_type: str | None = None
    original_filename: str | None = None

    def __post_init__(self) -> None:
        """Require an aware expiration and non-blank private storage key."""
        if (
            self.expires_at.tzinfo is None
            or not self.storage_path
            or self.storage_path.isspace()
        ):
            raise ValueError("Publication media metadata is invalid.")


@dataclass(frozen=True, slots=True)
class PublicationPayload:
    """Carry destination-ready content without SDK or administrator metadata."""

    destination_id: int
    text: str | None
    entities: tuple[TelegramEntity, ...]
    media: tuple[PublicationMedia, ...] = ()


class PublisherError(Exception):
    """Expose a safe provider-neutral publisher failure classification."""

    def __init__(
        self,
        category: PublicationFailureCategory,
        *,
        request_may_have_reached_telegram: bool = False,
        retry_after_seconds: float | None = None,
    ) -> None:
        """Store a safe category and send-boundary certainty."""
        super().__init__("Destination publication failed.")
        self.category = category
        self.request_may_have_reached_telegram = request_may_have_reached_telegram
        self.retry_after_seconds = retry_after_seconds


class PublicationClaimOutcome(StrEnum):
    """Describe the atomic claim result."""

    CLAIMED = "claimed"
    BUSY = "busy"
    TERMINAL = "terminal"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class PublicationClaimResult:
    """Return the canonical publication and claim decision."""

    outcome: PublicationClaimOutcome
    publication: Publication


class TelegramPublisherGateway(Protocol):
    """Publish destination-ready text or media through the User API."""

    async def publish(
        self, payload: PublicationPayload, *, timeout_seconds: float
    ) -> PublishedMessage:
        """Publish exactly one logical destination message or album."""
        ...


class PublicationPayloadLoader(Protocol):
    """Load one prepared text/Media payload without exposing persistence details."""

    async def load(self, post_id: str, destination_id: int) -> PublicationPayload:
        """Load or reject an incomplete destination artifact."""
        ...


class PublicationRepository(Protocol):
    """Own atomic publication claim and terminal transition operations."""

    async def claim(
        self,
        *,
        publication_id: str,
        post_id: str,
        destination_id: int,
        owner: str,
        now: datetime,
        lease_until: datetime,
        max_attempts: int,
        correlation_id: str,
        action: str = "immediate",
    ) -> PublicationClaimResult:
        """Claim a new, retry-ready, or expired publication atomically."""
        ...

    async def complete(
        self,
        publication_id: str,
        *,
        owner: str,
        result: PublishedMessage,
    ) -> Publication:
        """Record a successful owned attempt."""
        ...

    async def fail(
        self,
        publication_id: str,
        *,
        owner: str,
        category: PublicationFailureCategory,
        now: datetime,
        next_attempt_at: datetime | None,
        outcome_unknown: bool,
    ) -> Publication:
        """Record a safe retry or terminal failure."""
        ...


__all__ = (
    "PublicationClaimOutcome",
    "PublicationClaimResult",
    "PublicationMedia",
    "PublicationPayload",
    "PublicationPayloadLoader",
    "PublicationRepository",
    "PublisherError",
    "TelegramPublisherGateway",
)
