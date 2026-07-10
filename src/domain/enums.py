"""Domain enumerations shared across all layers."""

from __future__ import annotations

from enum import Enum


class PostCategory(str, Enum):
    """AI-assigned category of a collected Telegram post."""

    GENERAL_NEWS = "general_news"
    BREAKING_NEWS = "breaking_news"
    TECHNOLOGY = "technology"
    WAR = "war"
    VPN = "vpn"
    VPN_CONFIG = "vpn_config"
    IRRELEVANT = "irrelevant"


class VpnProtocol(str, Enum):
    """Supported proxy and VPN configuration protocols."""

    VMESS = "vmess"
    VLESS = "vless"
    SHADOWSOCKS = "ss"
    SHADOWSOCKS_R = "ssr"
    TROJAN = "trojan"
    HYSTERIA2 = "hysteria2"
    TUIC = "tuic"


class VpnTestStatus(str, Enum):
    """Connectivity test state of a single VPN configuration."""

    PENDING = "pending"
    WORKING = "working"
    FAILED = "failed"
    UNSUPPORTED = "unsupported"


class ChannelKind(str, Enum):
    """Kind of a destination channel, used for routing posts."""

    NEWS = "news"
    BREAKING = "breaking"
    TECHNOLOGY = "technology"
    VPN = "vpn"


class QueueItemType(str, Enum):
    """Type of a background queue item."""

    QUALITY_SCORE = "quality_score"
    QUALITY_SCORE_UPDATE = "quality_score_update"
    SOURCE_METRICS_REFRESH = "source_metrics_refresh"
    VPN_TEST = "vpn_test"
    APPROVAL_REQUEST = "approval_request"
    SCHEDULED_PUBLISH = "scheduled_publish"


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


class MediaDownloadStatus(str, Enum):
    """Download completeness for one collected Telegram post."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    FAILED = "failed"


class IngestionMode(str, Enum):
    """Origin pipeline used to ingest a Telegram post."""

    CONFIGURED_SOURCE = "configured_source"
    DIALOG_VPN_DISCOVERY = "dialog_vpn_discovery"


class QualityScoreStatus(str, Enum):
    """Lifecycle state of advisory quality scoring for one post."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    SCORED = "scored"
    UNAVAILABLE = "unavailable"


class SourceMetricsStatus(str, Enum):
    """Lifecycle state of delayed Telegram source-metric refresh."""

    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    REFRESHED = "refreshed"
    UNAVAILABLE = "unavailable"
