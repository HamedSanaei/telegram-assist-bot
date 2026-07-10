"""Custom exception types used across all application layers.

Every expected failure case has an explicit exception type so that
callers can react precisely instead of catching broad ``Exception``.
"""


class AppError(Exception):
    """Base class for all application-specific errors."""


class ConfigurationError(AppError):
    """Raised when configuration is missing, malformed, or invalid."""


class AiProviderError(AppError):
    """Raised when a single AI provider call fails or returns invalid data."""


class DuplicateDetectionError(AppError):
    """Raised when duplicate detection fails on all configured AI providers."""


class PostClassificationError(AppError):
    """Raised when post classification fails on all configured AI providers."""


class QualityScoringError(AppError):
    """Raised when quality scoring fails on all configured AI providers."""


class VpnTextCleanupError(AppError):
    """Raised when all AI providers fail to clean a VPN discovery post."""


class TelegramPublishError(AppError):
    """Raised when publishing a message to a Telegram channel fails."""


class VpnConfigParseError(AppError):
    """Raised when a vmess/vless configuration string cannot be parsed."""


class VpnConnectivityTestError(AppError):
    """Raised when a VPN connectivity test cannot be executed."""


class RepositoryError(AppError):
    """Raised when a persistence operation fails unexpectedly."""


class ApprovalStateError(AppError):
    """Raised when an approval action is invalid for the current state."""


class InvalidPostError(AppError):
    """Raised when a post is empty or otherwise unusable."""


class PriceFetchError(AppError):
    """Raised when fetching the USD price from the configured source fails."""
