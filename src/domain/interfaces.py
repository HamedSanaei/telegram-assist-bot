"""Domain interfaces (ports) and AI/VPN result value objects.

Application services depend only on these protocols. Concrete
implementations live in the infrastructure and presentation layers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from src.domain.entities import (
    AdminUser,
    DestinationChannel,
    DollarPrice,
    Post,
    QueueItem,
    VpnConfig,
)
from src.domain.enums import PostCategory, QueueItemType, QueueStatus


@dataclass(frozen=True)
class AiClassificationResult:
    """Result of classifying a post with an AI provider."""

    category: PostCategory
    provider: str
    confidence: float | None = None


@dataclass(frozen=True)
class DuplicateCheckResult:
    """Result of an AI duplicate/near-duplicate check."""

    is_duplicate: bool
    provider: str
    matched_index: int | None = None


@dataclass(frozen=True)
class VpnTestResult:
    """Result of one VPN connectivity test executed on the Iran worker."""

    working: bool
    latency_ms: int | None = None
    error: str | None = None


class AiProvider(Protocol):
    """Interface for AI providers used for classification and dedup."""

    name: str

    async def classify_post(self, text: str) -> AiClassificationResult:
        """
        Classify a Telegram post.

        Args:
            text: Raw post text.

        Returns:
            The AI classification result.

        Raises:
            AiProviderError: When the provider fails or replies invalidly.
        """
        ...

    async def is_duplicate(
        self, new_text: str, existing_texts: list[str]
    ) -> DuplicateCheckResult:
        """
        Check whether a new post duplicates any of the existing texts.

        Args:
            new_text: New post text.
            existing_texts: Existing post texts to compare against.

        Returns:
            Duplicate check result.

        Raises:
            AiProviderError: When the provider fails or replies invalidly.
        """
        ...


class PostRepository(Protocol):
    """Persistence port for collected posts (MongoDB in production)."""

    async def save(self, post: Post) -> None:
        """Insert or replace a post document."""
        ...

    async def get(self, post_id: str) -> Post | None:
        """Return a post by internal id, or ``None`` when absent/expired."""
        ...

    async def find_by_content_hash(self, content_hash: str) -> Post | None:
        """Return a stored post with the same content hash, if any."""
        ...

    async def list_recent_texts(self, limit: int) -> list[str]:
        """Return texts of the most recently collected posts."""
        ...

    async def update_vpn_configs(self, post_id: str, configs: list[VpnConfig]) -> None:
        """Persist updated VPN config test results for a post."""
        ...

    async def delete_expired(self, now: datetime) -> int:
        """Delete posts whose ``expires_at`` passed; return deleted count."""
        ...


class QueueRepository(Protocol):
    """Persistence port for the SQLite-backed background job queue."""

    async def enqueue(
        self,
        item_type: QueueItemType,
        payload: dict[str, object],
        scheduled_at: datetime | None = None,
    ) -> int:
        """Insert a new pending queue item and return its id."""
        ...

    async def claim_next_due(self, now: datetime) -> QueueItem | None:
        """
        Atomically claim the next due pending item.

        The claimed item is switched to ``processing`` and its attempt
        counter is incremented, so two workers never process the same
        item concurrently.
        """
        ...

    async def mark_status(
        self, item_id: int, status: QueueStatus, last_error: str | None = None
    ) -> None:
        """Set the final status of a queue item."""
        ...

    async def reschedule(
        self, item_id: int, scheduled_at: datetime, last_error: str
    ) -> None:
        """Return a failed item to ``pending`` for a later retry."""
        ...

    async def expire_older_than(self, cutoff: datetime) -> int:
        """Mark stale unfinished items as expired; return affected count."""
        ...


class ChannelRepository(Protocol):
    """Persistence port for source and destination channels."""

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        """Insert or update a destination channel."""
        ...

    async def list_destinations(self) -> list[DestinationChannel]:
        """Return all enabled destination channels."""
        ...

    async def list_price_channels(self) -> list[DestinationChannel]:
        """Return enabled channels that receive USD price posts."""
        ...

    async def upsert_source(self, identifier: str) -> None:
        """Insert or update a source channel identifier."""
        ...

    async def upsert_source_details(
        self,
        identifier: str,
        chat_id: int,
        title: str,
        username: str,
    ) -> None:
        """Update a resolved source channel's display metadata."""
        ...

    async def get_source_label(self, chat_id: int) -> str | None:
        """Return a readable source label by Telegram chat id, if known."""
        ...

    async def list_sources(self) -> list[str]:
        """Return all enabled source channel identifiers."""
        ...


class AdminRepository(Protocol):
    """Persistence port for admin users."""

    async def upsert(self, admin: AdminUser) -> None:
        """Insert or update an admin user."""
        ...

    async def is_admin(self, telegram_user_id: int) -> bool:
        """Return whether the given Telegram user id is an admin."""
        ...

    async def list_user_ids(self) -> list[int]:
        """Return all admin Telegram user ids."""
        ...


class PublishLogRepository(Protocol):
    """Persistence port for the publish log (prevents double publishing)."""

    async def is_published(self, post_id: str, channel_chat_id: int) -> bool:
        """Return whether the post was already published to the channel."""
        ...

    async def record_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        """Record a successful publish of a post to a channel."""
        ...

    async def published_channels(self, post_id: str) -> set[int]:
        """Return chat ids of channels the post was already published to."""
        ...


class PriceHistoryRepository(Protocol):
    """Persistence port for the USD price history."""

    async def save(self, price: DollarPrice) -> int:
        """Store a new price observation and return its id."""
        ...

    async def get_latest(self) -> DollarPrice | None:
        """Return the most recently stored price, or ``None``."""
        ...


class VpnConnectivityTester(Protocol):
    """Port for testing a VPN config from the Iran network."""

    async def test(self, config: VpnConfig) -> VpnTestResult:
        """
        Test one VPN configuration.

        Raises:
            VpnConnectivityTestError: When the test cannot be executed.
        """
        ...


class MessagePublisher(Protocol):
    """Port for publishing content to Telegram channels."""

    async def publish_text(self, chat_id: int, text: str) -> int:
        """
        Send a plain text message; return the Telegram message id.

        Raises:
            TelegramPublishError: When sending fails.
        """
        ...

    async def publish_post(self, chat_id: int, post: Post) -> int:
        """
        Publish a collected post (text and media); return the message id.

        Raises:
            TelegramPublishError: When sending fails.
        """
        ...


class ApprovalNotifier(Protocol):
    """Port for sending approval requests to admins (approval bot)."""

    async def send_approval_request(
        self, post: Post, channels: list[DestinationChannel]
    ) -> None:
        """Send the approval message with channel buttons to all admins."""
        ...


class PriceSource(Protocol):
    """Port for fetching the current USD price."""

    name: str

    async def fetch_price(self) -> "DollarPrice":
        """
        Fetch the current USD price.

        Raises:
            PriceFetchError: When the source is unreachable or invalid.
        """
        ...
