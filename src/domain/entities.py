"""Core domain entities and value objects.

This module must stay free of any framework, database, Telegram, or
external API dependency. Only the Python standard library is allowed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from src.domain.enums import (
    ChannelKind,
    IngestionMode,
    MediaDownloadStatus,
    MediaKind,
    PostCategory,
    QualityScoreStatus,
    QueueItemType,
    QueueStatus,
    SourceMetricsStatus,
    VpnProtocol,
    VpnTestStatus,
)


@dataclass
class MediaItem:
    """
    A media attachment stored on disk for a collected post.

    Attributes:
        kind: The media kind (photo, video, document).
        file_path: Local filesystem path of the downloaded file.
        mime_type: Optional MIME type reported by Telegram.
        file_size: Optional file size in bytes.
    """

    kind: MediaKind
    file_path: str
    mime_type: str | None = None
    file_size: int | None = None


@dataclass
class TextEntity:
    """
    Framework-neutral formatted-text entity captured from Telegram.

    Attributes:
        kind: Entity kind. Currently ``"custom_emoji"`` is used for
            Telegram premium/custom emoji preservation.
        offset: Entity start offset as reported by Telegram.
        length: Entity length as reported by Telegram.
        data: Provider-specific metadata, such as ``document_id`` for a
            custom emoji entity.
    """

    kind: str
    offset: int
    length: int
    data: dict[str, object] = field(default_factory=dict)


@dataclass
class PostSourceMetrics:
    """
    Telegram-side engagement metadata captured from a source message.

    Attributes:
        views: Telegram view count when available.
        forwards: Telegram forward/share count when available.
        replies_count: Reply/comment count when available.
        reactions_count: Sum of visible reaction counts when available.
        source_published_at: UTC timestamp of the original source message.
    """

    views: int | None = None
    forwards: int | None = None
    replies_count: int | None = None
    reactions_count: int | None = None
    source_published_at: datetime | None = None


@dataclass
class PostQualityScore:
    """
    AI-generated quality score used to help admins decide on reposting.

    Attributes:
        score: Suggested repost value from 0 to 100.
        reason: Short Persian reason shown in the approval preview.
        provider: AI provider that produced the score.
        scored_at: UTC timestamp when scoring was completed.
        metrics: Raw source metrics used as part of scoring.
    """

    score: float
    reason: str
    provider: str
    scored_at: datetime | None = None
    metrics: dict[str, object] = field(default_factory=dict)


@dataclass
class VpnConfig:
    """
    A vmess/vless configuration extracted from a post.

    Attributes:
        protocol: The VPN protocol (vmess or vless).
        raw: The original configuration URI exactly as posted.
        host: Server address.
        port: Server port.
        user_id: The client UUID.
        transport: Transport network such as ``tcp``, ``ws``, or ``grpc``.
        security: Security layer such as ``tls`` or ``reality``.
        remark: Human-readable label attached to the config.
        extra: Protocol-specific fields (ws path, sni, reality keys, ...).
        test_status: Result of the Iran connectivity test.
    """

    protocol: VpnProtocol
    raw: str
    host: str
    port: int
    user_id: str
    transport: str | None = None
    security: str | None = None
    remark: str | None = None
    extra: dict[str, str] = field(default_factory=dict)
    test_status: VpnTestStatus = VpnTestStatus.PENDING


@dataclass
class Post:
    """
    A collected Telegram post held temporarily until it expires.

    Attributes:
        post_id: Internal unique identifier (uuid4 hex).
        source_chat_id: Telegram chat id of the source channel.
        source_message_id: Telegram message id inside the source channel.
        grouped_id: Telegram album/group id, when the post came from an album.
        text: Raw post text, preserved exactly (UTF-8).
        text_entities: Formatting entities attached to ``text``.
        content_hash: Hash of the normalized text used for exact dedup.
        media: Downloaded media attachments.
        category: AI-assigned category, if classified.
        ai_provider: Name of the AI provider that classified the post.
        is_duplicate: Whether AI judged this post as a duplicate.
        duplicate_of: Post id of the matched duplicate, when known.
        duplicate_provider: AI provider that produced the duplicate decision.
        skipped_reason: Reason a stored post is intentionally not sent onward.
        source_metrics: Source-side Telegram engagement metrics.
        quality_score: Suggested repost quality score, if calculated.
        vpn_configs: Extracted vmess/vless configs, if any.
        collected_at: UTC time the post was collected.
        expires_at: UTC time after which the post is removed (14 days).
    """

    post_id: str
    source_chat_id: int
    source_message_id: int
    text: str
    content_hash: str
    source_label: str = ""
    ingestion_mode: IngestionMode = IngestionMode.CONFIGURED_SOURCE
    quality_score_status: QualityScoreStatus = QualityScoreStatus.PENDING
    source_metrics_status: SourceMetricsStatus = SourceMetricsStatus.PENDING
    vpn_fingerprints: list[str] = field(default_factory=list)
    text_entities: list[TextEntity] = field(default_factory=list)
    grouped_id: int | None = None
    media: list[MediaItem] = field(default_factory=list)
    expected_media_count: int = 0
    media_download_status: MediaDownloadStatus = MediaDownloadStatus.COMPLETE
    category: PostCategory | None = None
    ai_provider: str | None = None
    is_duplicate: bool = False
    duplicate_of: str | None = None
    duplicate_provider: str | None = None
    skipped_reason: str | None = None
    source_metrics: PostSourceMetrics = field(default_factory=PostSourceMetrics)
    quality_score: PostQualityScore | None = None
    vpn_configs: list[VpnConfig] = field(default_factory=list)
    collected_at: datetime | None = None
    expires_at: datetime | None = None

    def has_working_vpn_config(self) -> bool:
        """
        Return ``True`` when at least one extracted VPN config passed
        the Iran connectivity test.
        """
        return any(c.test_status == VpnTestStatus.WORKING for c in self.vpn_configs)


@dataclass
class DestinationChannel:
    """
    A Telegram channel the system may publish approved posts to.

    Attributes:
        chat_id: Telegram chat id of the channel.
        title: Display title used on approval buttons.
        public_id: Public channel username/link used when replacing
            source-channel mentions inside republished text.
        kind: Channel kind used for routing (news, vpn, ...).
        publish_usd_price: Whether scheduled USD price posts go here.
        enabled: Whether the channel is currently active.
        post_interval_minutes: Legacy per-channel pacing setting retained
            for management commands; native scheduled posts currently use
            a fixed five-minute slot policy.
    """

    chat_id: int
    title: str
    public_id: str = ""
    kind: ChannelKind = ChannelKind.NEWS
    publish_usd_price: bool = False
    enabled: bool = True
    post_interval_minutes: int = 30


@dataclass
class AdminUser:
    """An administrator allowed to approve and publish posts."""

    telegram_user_id: int
    name: str = ""


@dataclass
class QueueItem:
    """
    A persisted background job stored in the SQLite queue.

    Attributes:
        id: Auto-increment row id (``None`` until persisted).
        type: The job type.
        status: Current lifecycle status.
        payload: JSON-serializable job arguments (e.g. ``{"post_id": ...}``).
        attempts: Number of processing attempts so far.
        last_error: Message of the last failure, if any.
        scheduled_at: UTC time the item becomes due.
        created_at: UTC creation time.
        updated_at: UTC last update time.
    """

    type: QueueItemType
    payload: dict[str, object]
    id: int | None = None
    status: QueueStatus = QueueStatus.PENDING
    attempts: int = 0
    last_error: str | None = None
    scheduled_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class PublishRecord:
    """
    Record of one post published to one destination channel.

    Attributes:
        post_id: Internal id of the published post.
        channel_chat_id: Destination channel chat id.
        message_id: Telegram message id of the published message.
        published_at: UTC publish time.
    """

    post_id: str
    channel_chat_id: int
    message_id: int | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class PublishLogEntry:
    """
    Persisted publish/schedule state for one post and destination channel.

    Attributes:
        post_id: Internal collected post id.
        channel_chat_id: Destination Telegram channel id.
        mode: Delivery mode, either ``"immediate"`` or ``"scheduled"``.
        status: State such as ``"reserved"``, ``"published"``,
            ``"scheduled"``, or ``"removed"``.
        message_id: Telegram message id returned by the publish/schedule call.
        published_at: UTC time when the state was last activated.
        scheduled_at: UTC Telegram schedule time for native scheduled posts.
        removed_at: UTC time when the published/scheduled message was removed.
    """

    post_id: str
    channel_chat_id: int
    mode: str
    status: str
    message_id: int | None = None
    published_at: datetime | None = None
    scheduled_at: datetime | None = None
    removed_at: datetime | None = None


@dataclass
class ApprovalMessageRef:
    """
    A Telegram approval-bot message carrying an inline keyboard.

    Attributes:
        post_id: Internal id of the post shown in the approval message.
        admin_user_id: Telegram user id of the admin recipient.
        chat_id: Chat id where the approval message was sent.
        message_id: Telegram message id that owns the inline keyboard.
        delivery_mode: Current per-message delivery mode (``"s"`` or ``"i"``).
        active: Whether the message can still be edited.
        id: Optional SQLite row id.
    """

    post_id: str
    admin_user_id: int
    chat_id: int
    message_id: int
    delivery_mode: str = "s"
    preview_kind: str = "text"
    active: bool = True
    id: int | None = None


@dataclass(frozen=True)
class ApprovalPreviewRefreshResult:
    """Outcome of editing tracked approval previews in place."""

    updated: int = 0
    retryable_failures: int = 0
    permanent_failures: int = 0


@dataclass(frozen=True)
class RecurringForwardOccurrence:
    """
    Operational state for one recurring native Telegram schedule occurrence.

    Attributes:
        campaign_id: Stable configuration campaign id.
        destination_chat_id: Destination Telegram channel id.
        source_post_url: Telegram source-post URL.
        show_forward_header: Whether Telegram's forwarded-from header is kept.
        scheduled_at: UTC occurrence time.
        status: ``reserved``, ``scheduled``, ``failed``, or ``cancelled``.
        message_ids: Native Telegram scheduled message ids.
        last_error: Last scheduling or cancellation error, when present.
        id: Optional SQLite row id.
    """

    campaign_id: str
    destination_chat_id: int
    source_post_url: str
    show_forward_header: bool
    scheduled_at: datetime
    status: str = "reserved"
    message_ids: tuple[int, ...] = ()
    last_error: str | None = None
    id: int | None = None


@dataclass
class DollarPrice:
    """
    One recorded USD price observation.

    Attributes:
        price: The price value (e.g. in Toman).
        source: Name of the price source.
        fetched_at: UTC time the price was fetched.
        id: Auto-increment row id (``None`` until persisted).
    """

    price: Decimal
    source: str
    fetched_at: datetime | None = None
    id: int | None = None
