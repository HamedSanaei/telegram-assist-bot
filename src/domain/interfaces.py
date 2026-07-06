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
    ApprovalMessageRef,
    DestinationChannel,
    DollarPrice,
    Post,
    PublishLogEntry,
    PostSourceMetrics,
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
class AiPostAnalysisResult:
    """Combined AI duplicate-check and classification result."""

    category: PostCategory
    is_duplicate: bool
    provider: str
    matched_index: int | None = None
    is_advertisement: bool = False
    reason: str = ""


@dataclass(frozen=True)
class QualityScoreResult:
    """Result of scoring a collected post for repost quality."""

    score: float
    reason: str
    provider: str
    raw_metrics: dict[str, object]


@dataclass(frozen=True)
class VpnTestResult:
    """Result of one VPN connectivity test executed on the Iran worker."""

    working: bool
    latency_ms: int | None = None
    error: str | None = None


class AiProvider(Protocol):
    """Interface for AI providers used by the post intelligence pipeline."""

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

    async def analyze_post(
        self, new_text: str, existing_texts: list[str]
    ) -> AiPostAnalysisResult:
        """
        Classify a post and check duplicates in one provider request.

        Args:
            new_text: New post text.
            existing_texts: Existing post texts to compare against.

        Returns:
            Combined duplicate and classification result.

        Raises:
            AiProviderError: When the provider fails or replies invalidly.
        """
        ...

    async def score_post(
        self,
        text: str,
        category: PostCategory | None,
        metrics: dict[str, object],
    ) -> QualityScoreResult:
        """
        Score a collected post's repost value from 0 to 100.

        Args:
            text: Raw post text.
            category: Classification category, if available.
            metrics: Source engagement and timing metrics.

        Returns:
            The quality score result.

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

    async def find_by_source_message(
        self, source_chat_id: int, source_message_id: int, grouped_id: int | None
    ) -> Post | None:
        """Return a stored post by Telegram source message identity."""
        ...

    async def list_recent_texts(self, limit: int) -> list[str]:
        """Return recent non-skipped, non-duplicate post texts."""
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

    async def has_active_or_successful_post_item(
        self, post_id: str, item_types: set[QueueItemType]
    ) -> bool:
        """
        Return whether a post already has an active or successful queue item.

        Used by the collector when it sees a stored post during same-day
        backfill. A post that was saved but never reached approval should be
        requeued, while a post that already has a pending/processing/completed
        pipeline item should not be duplicated.
        """
        ...

    async def latest_scheduled_publish_for_channel(
        self, channel_chat_id: int
    ) -> datetime | None:
        """
        Return the latest ``scheduled_at`` of a pending scheduled-publish
        item targeting the channel, or ``None`` when the channel queue
        is empty. Used to pace posts per channel.
        """
        ...

    async def scheduled_publish_channels(self, post_id: str) -> set[int]:
        """Return chat ids with a pending scheduled publish of this post."""
        ...


class ChannelRepository(Protocol):
    """Persistence port for source and destination channels."""

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        """Insert or update a destination channel."""
        ...

    async def disable_destinations_except(self, chat_ids: set[int]) -> int:
        """Disable destinations whose chat ids are not in ``chat_ids``."""
        ...

    async def list_destinations(self) -> list[DestinationChannel]:
        """Return all enabled destination channels."""
        ...

    async def get_destination(self, chat_id: int) -> DestinationChannel | None:
        """Return one destination channel (enabled or not), or ``None``."""
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

    async def list_source_usernames(self) -> list[str]:
        """Return resolved public usernames of enabled source channels."""
        ...

    async def disable_source(self, identifier: str) -> bool:
        """Disable a source channel; return whether a row was affected."""
        ...

    async def disable_sources_except(self, identifiers: set[str]) -> int:
        """Disable sources whose identifiers are not in ``identifiers``."""
        ...


class AdminRepository(Protocol):
    """Persistence port for admin users."""

    async def upsert(self, admin: AdminUser) -> None:
        """Insert or update an admin user."""
        ...

    async def replace_all(self, admins: list[AdminUser]) -> None:
        """Make the admin table exactly match ``admins``."""
        ...

    async def is_admin(self, telegram_user_id: int) -> bool:
        """Return whether the given Telegram user id is an admin."""
        ...

    async def list_user_ids(self) -> list[int]:
        """Return all admin Telegram user ids."""
        ...


class PublishLogRepository(Protocol):
    """Persistence port for the publish log (prevents double publishing)."""

    async def has_any_delivery_record(self, post_id: str) -> bool:
        """
        Return whether a post has any destination delivery history.

        Any publish-log row means the post has already been touched by an
        admin action, including reserved, published, scheduled, or removed
        states. Startup approval repair must not resend such posts.
        """
        ...

    async def is_published(self, post_id: str, channel_chat_id: int) -> bool:
        """Return whether the post was already published to the channel."""
        ...

    async def record_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        """Record a successful publish of a post to a channel."""
        ...

    async def try_reserve_publish(
        self, post_id: str, channel_chat_id: int, mode: str
    ) -> bool:
        """
        Atomically reserve a post/channel pair before Telegram publishing.

        Returns:
            ``True`` if this caller owns the reservation, otherwise
            ``False`` when the pair was already reserved or published.
        """
        ...

    async def mark_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        """Mark a reserved post/channel pair as successfully published."""
        ...

    async def mark_scheduled(
        self,
        post_id: str,
        channel_chat_id: int,
        message_id: int,
        scheduled_at: datetime,
    ) -> None:
        """Mark a reserved post/channel pair as natively scheduled."""
        ...

    async def release_reservation(self, post_id: str, channel_chat_id: int) -> None:
        """Remove an unpublished reservation after a Telegram send failure."""
        ...

    async def published_channels(self, post_id: str) -> set[int]:
        """Return chat ids of channels the post was already published to."""
        ...

    async def scheduled_channels(self, post_id: str) -> set[int]:
        """Return chat ids of channels the post was already scheduled to."""
        ...

    async def get_active_record(
        self, post_id: str, channel_chat_id: int
    ) -> PublishLogEntry | None:
        """Return the active publish/schedule record for a post/channel."""
        ...

    async def mark_removed(self, post_id: str, channel_chat_id: int) -> None:
        """Mark a published or scheduled post/channel pair as removed."""
        ...

    async def last_published_at(self, channel_chat_id: int) -> datetime | None:
        """Return the most recent publish time on the channel, or ``None``."""
        ...


class ApprovalRequestRepository(Protocol):
    """Persistence port for approval request dispatch idempotency."""

    async def has_requested(self, post_id: str) -> bool:
        """Return whether the post already entered the approval dispatch stage."""
        ...

    async def record_requested(self, post_id: str) -> None:
        """Record that the post was sent to the approval bot."""
        ...

    async def reserve_request(self, post_id: str) -> bool:
        """
        Reserve an approval dispatch attempt for a post.

        Returns:
            ``True`` when this caller may send the approval preview. Returns
            ``False`` when the post is already reserved or was already sent.
            Failed dispatches may be reserved again by queue retry.
        """
        ...

    async def mark_sent(self, post_id: str) -> None:
        """Mark a reserved approval request as successfully sent."""
        ...

    async def mark_failed(self, post_id: str, error: str) -> None:
        """Mark a reserved approval request as failed for queue retry."""
        ...

    async def list_requested_post_ids(self) -> list[str]:
        """Return post ids that have been recorded as approval-requested."""
        ...


class ApprovalMessageRepository(Protocol):
    """Persistence port for approval-bot messages sent to admins."""

    async def record_messages(self, refs: list[ApprovalMessageRef]) -> None:
        """Persist delivered approval message references."""
        ...

    async def list_active(self, post_id: str) -> list[ApprovalMessageRef]:
        """Return active approval messages for a post."""
        ...

    async def set_delivery_mode(
        self, post_id: str, chat_id: int, message_id: int, delivery_mode: str
    ) -> None:
        """Persist a per-message delivery mode change."""
        ...

    async def deactivate(self, message_ref_id: int) -> None:
        """Mark an approval message as inactive after edit failure."""
        ...

    async def list_active_post_ids(self) -> list[str]:
        """Return post ids that still have active approval messages."""
        ...

    async def deactivate_admins_except(self, admin_user_ids: set[int]) -> int:
        """Deactivate approval messages belonging to removed admins."""
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

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """
        Delete a message previously published to a destination channel.

        Raises:
            TelegramPublishError: When Telegram rejects deletion.
        """
        ...


class ScheduledMessagePublisher(Protocol):
    """Port for uploading posts into Telegram's native channel schedule."""

    async def latest_scheduled_at(self, chat_id: int) -> datetime | None:
        """
        Return the latest native Telegram scheduled message time for a channel.

        Args:
            chat_id: Destination channel chat id.

        Returns:
            The latest scheduled UTC datetime, or ``None`` when Telegram
            has no visible scheduled messages or the lookup is unavailable.
        """
        ...

    async def schedule_post(self, chat_id: int, post: Post, scheduled_at: datetime) -> int:
        """
        Upload a post into Telegram's native channel schedule.

        Args:
            chat_id: Destination channel chat id.
            post: The approved post.
            scheduled_at: UTC datetime passed to Telegram as schedule date.

        Returns:
            Telegram message id of the scheduled message when available.

        Raises:
            TelegramPublishError: When Telegram rejects scheduling.
        """
        ...

    async def delete_scheduled_message(self, chat_id: int, message_id: int) -> None:
        """
        Delete a message from Telegram's native scheduled posts.

        Raises:
            TelegramPublishError: When Telegram rejects deletion.
        """
        ...


class SourceMetadataRefresher(Protocol):
    """Port for refreshing source message engagement metrics from Telegram."""

    async def refresh_metrics(
        self, source_chat_id: int, source_message_id: int
    ) -> PostSourceMetrics | None:
        """
        Refresh source-side engagement metrics for one collected message.

        Args:
            source_chat_id: Source Telegram channel chat id.
            source_message_id: Message id inside the source channel.

        Returns:
            Refreshed metrics, or ``None`` if the message cannot be fetched.
        """
        ...


class ApprovalNotifier(Protocol):
    """Port for sending approval requests to admins (approval bot)."""

    async def send_approval_request(
        self, post: Post, channels: list[DestinationChannel]
    ) -> list[ApprovalMessageRef]:
        """Send approval messages and return delivered message references."""
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
