"""Domain enumerations shared across all layers."""

from __future__ import annotations

from enum import Enum


class PostCategory(str, Enum):
    """AI-assigned category of a collected Telegram post."""

    GENERAL_NEWS = "general_news"
    BREAKING_NEWS = "breaking_news"
    TECHNOLOGY = "technology"
    VPN = "vpn"
    VPN_CONFIG = "vpn_config"
    IRRELEVANT = "irrelevant"


class VpnProtocol(str, Enum):
    """Supported VPN configuration protocols."""

    VMESS = "vmess"
    VLESS = "vless"


class VpnTestStatus(str, Enum):
    """Connectivity test state of a single VPN configuration."""

    PENDING = "pending"
    WORKING = "working"
    FAILED = "failed"


class ChannelKind(str, Enum):
    """Kind of a destination channel, used for routing posts."""

    NEWS = "news"
    BREAKING = "breaking"
    TECHNOLOGY = "technology"
    VPN = "vpn"


class QueueItemType(str, Enum):
    """Type of a background queue item."""

    VPN_TEST = "vpn_test"
    APPROVAL_REQUEST = "approval_request"


class QueueStatus(str, Enum):
    """Lifecycle status of a background queue item."""

    PENDING = "pending"
    PROCESSING = "processing"
    WAITING_APPROVAL = "waiting_approval"
    APPROVED = "approved"
    PUBLISHED = "published"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    DUPLICATE = "duplicate"
    EXPIRED = "expired"


class MediaKind(str, Enum):
    """Kind of a media attachment on a collected post."""

    PHOTO = "photo"
    VIDEO = "video"
    DOCUMENT = "document"
