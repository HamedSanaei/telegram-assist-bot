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
    MediaKind,
    PostCategory,
    QueueItemType,
    QueueStatus,
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
        text: Raw post text, preserved exactly (UTF-8).
        content_hash: Hash of the normalized text used for exact dedup.
        media: Downloaded media attachments.
        category: AI-assigned category, if classified.
        ai_provider: Name of the AI provider that classified the post.
        vpn_configs: Extracted vmess/vless configs, if any.
        collected_at: UTC time the post was collected.
        expires_at: UTC time after which the post is removed (14 days).
    """

    post_id: str
    source_chat_id: int
    source_message_id: int
    text: str
    content_hash: str
    media: list[MediaItem] = field(default_factory=list)
    category: PostCategory | None = None
    ai_provider: str | None = None
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
    """

    chat_id: int
    title: str
    public_id: str = ""
    kind: ChannelKind = ChannelKind.NEWS
    publish_usd_price: bool = False
    enabled: bool = True


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
