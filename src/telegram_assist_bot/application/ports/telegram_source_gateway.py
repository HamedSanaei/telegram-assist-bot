"""Application-owned contracts for Telegram User API authentication."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, ClassVar, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from datetime import datetime

    from telegram_assist_bot.domain.media import MediaType
    from telegram_assist_bot.domain.posts import TelegramEntity


class TelegramGatewayError(Exception):
    """Base class for safe Telegram boundary failures."""

    error_category: ClassVar[str] = "permanent"
    safe_message: ClassVar[str] = "Telegram operation failed."

    def __init__(self, *, cause: BaseException | None = None) -> None:
        """Initialize a fixed safe message while retaining an optional cause."""
        super().__init__(self.safe_message)
        if cause is not None:
            self.__cause__ = cause


class TelegramSessionInvalidError(TelegramGatewayError):
    """Report a revoked or otherwise unusable existing session."""

    error_category = "authorization"
    safe_message = (
        "Telegram session is invalid; explicit re-authentication is required."
    )


class TelegramInvalidCodeError(TelegramGatewayError):
    """Report an invalid or expired verification code."""

    error_category = "authorization"
    safe_message = "Telegram verification code is invalid or expired."


class TelegramInvalidPasswordError(TelegramGatewayError):
    """Report an invalid two-factor authentication password."""

    error_category = "authorization"
    safe_message = "Telegram two-factor authentication failed."


class TelegramSessionMutationConflictError(TelegramGatewayError):
    """Report another process currently mutating the same session."""

    error_category = "concurrency_conflict"
    safe_message = "Telegram session is already being updated by another process."


class TelegramTransientError(TelegramGatewayError):
    """Report a retryable Telegram network failure."""

    error_category = "transient"
    safe_message = "Telegram is temporarily unavailable."


class TelegramOperationTimeoutError(TelegramGatewayError):
    """Report a bounded Telegram operation timeout."""

    error_category = "timeout"
    safe_message = "Telegram operation timed out."


class TelegramRateLimitError(TelegramGatewayError):
    """Report a bounded Telegram flood-wait response."""

    error_category = "rate_limit"
    safe_message = "Telegram rate limit was reached."

    def __init__(self, retry_after_seconds: int, *, cause: BaseException | None = None):
        """Retain only the bounded non-secret retry delay."""
        if type(retry_after_seconds) is not int or retry_after_seconds < 0:
            raise ValueError("retry_after_seconds must be a non-negative integer")
        self.retry_after_seconds = retry_after_seconds
        super().__init__(cause=cause)


class TelegramChannelNotFoundError(TelegramGatewayError):
    """Report a configured Telegram channel that cannot be resolved."""

    safe_message = "A configured Telegram channel could not be resolved."


class TelegramChannelPermissionError(TelegramGatewayError):
    """Report insufficient access to a configured Telegram channel."""

    error_category = "permission"
    safe_message = "Telegram channel permission is insufficient."


class TelegramSessionStatus(StrEnum):
    """Describe the non-interactive authorization state of one session."""

    MISSING = "Missing"
    AUTHORIZED = "Authorized"
    INVALID = "Invalid"


class TelegramLoginStep(StrEnum):
    """Describe the next step after submitting a verification code."""

    AUTHORIZED = "Authorized"
    TWO_FACTOR_PASSWORD_REQUIRED = "TwoFactorPasswordRequired"  # noqa: S105  # pragma: allowlist secret


class TelegramChannelRole(StrEnum):
    """Identify how the application intends to use a configured channel."""

    SOURCE = "Source"
    DESTINATION = "Destination"


@dataclass(frozen=True, slots=True)
class TelegramAccount:
    """Expose only validation facts required from the authenticated account."""

    account_id: int
    is_premium: bool

    def __post_init__(self) -> None:
        """Require stable application-owned scalar values."""
        if type(self.account_id) is not int or self.account_id <= 0:
            raise ValueError("account_id must be a positive integer")
        if type(self.is_premium) is not bool:
            raise TypeError("is_premium must be a boolean")


@dataclass(frozen=True, slots=True)
class TelegramChannelReference:
    """Describe one configured channel without an SDK entity."""

    config_name: str
    configured_channel_id: int | None
    configured_username: str | None
    role: TelegramChannelRole
    configuration_path: str

    def __post_init__(self) -> None:
        """Validate identifiers while preserving their exact configured values."""
        if not self.config_name or self.config_name.isspace():
            raise ValueError("config_name must not be blank")
        if self.configured_channel_id is not None and (
            type(self.configured_channel_id) is not int
            or self.configured_channel_id == 0
        ):
            raise ValueError("configured_channel_id must be a non-zero integer")
        if self.configured_username is not None and (
            not self.configured_username or self.configured_username.isspace()
        ):
            raise ValueError("configured_username must be non-blank when present")
        if self.configured_channel_id is None and self.configured_username is None:
            raise ValueError("a channel identifier or username is required")
        if type(self.role) is not TelegramChannelRole:
            raise TypeError("role must be TelegramChannelRole")
        if not self.configuration_path or self.configuration_path.isspace():
            raise ValueError("configuration_path must not be blank")


@dataclass(frozen=True, slots=True)
class ResolvedTelegramChannel:
    """Return canonical channel identity and stable permission facts."""

    channel_id: int
    username: str | None
    display_name: str
    can_read: bool
    can_publish: bool

    def __post_init__(self) -> None:
        """Reject malformed adapter output without SDK-specific validation."""
        if type(self.channel_id) is not int or self.channel_id == 0:
            raise ValueError("channel_id must be a non-zero integer")
        if self.username is not None and (not self.username or self.username.isspace()):
            raise ValueError("username must be non-blank when present")
        if not self.display_name or self.display_name.isspace():
            raise ValueError("display_name must not be blank")


@dataclass(frozen=True, slots=True)
class TelegramMediaReference:
    """Describe downloadable media without exposing an SDK object."""

    media_type: MediaType
    item_index: int
    size_bytes: int | None
    mime_type: str | None
    original_filename: str | None
    opaque_reference: str
    media_group_id: str | None = None


@dataclass(frozen=True, slots=True)
class TelegramTextMessage:
    """Preserve one source message in an SDK-independent ingestion DTO."""

    source_channel_id: int
    source_channel_username: str | None
    source_channel_display_name: str
    source_message_id: int
    text: str | None
    caption: str | None
    text_entities: tuple[TelegramEntity, ...]
    caption_entities: tuple[TelegramEntity, ...]
    source_published_at: datetime
    is_service: bool
    has_media: bool
    media: tuple[TelegramMediaReference, ...] = ()

    def __repr__(self) -> str:
        """Hide source payload while retaining safe identity diagnostics."""
        return (
            "TelegramTextMessage("
            f"source_channel_id={self.source_channel_id}, "
            f"source_message_id={self.source_message_id}, payload=<redacted>)"
        )


@dataclass(frozen=True, slots=True)
class TelegramHistoryQuery:
    """Describe one bounded inclusive-start/exclusive-end history request."""

    source_channel_id: int
    start_inclusive: datetime
    end_exclusive: datetime
    page_size: int
    max_pages: int


@dataclass(frozen=True, slots=True)
class TelegramHistoryPage:
    """Return one SDK-token-free page of mapped source messages."""

    messages: tuple[TelegramTextMessage, ...]


@runtime_checkable
class TelegramAuthenticationGateway(Protocol):
    """Authenticate one Telegram user session without exposing SDK objects."""

    async def inspect_session(self) -> TelegramSessionStatus:
        """Inspect an existing session without prompting or deleting it."""
        ...

    async def begin_login(self, phone_number: str) -> None:
        """Acquire exclusive mutation ownership and request a login code."""
        ...

    async def submit_login_code(self, code: str) -> TelegramLoginStep:
        """Submit a verification code and report whether 2FA is required."""
        ...

    async def submit_two_factor_password(self, password: str) -> None:
        """Complete a pending login with the account 2FA password."""
        ...

    async def abort_login(self) -> None:
        """Release a pending login and its session mutation lock safely."""
        ...

    async def close(self) -> None:
        """Close the underlying Telegram client without changing authorization."""
        ...


@runtime_checkable
class TelegramValidationGateway(Protocol):
    """Validate one existing session and its configured channel access."""

    async def validate_account(self) -> TelegramAccount:
        """Return non-sensitive account facts without prompting."""
        ...

    async def resolve_channel(
        self,
        reference: TelegramChannelReference,
    ) -> ResolvedTelegramChannel:
        """Resolve one configured channel without fetching history or sending."""
        ...


@runtime_checkable
class TelegramHistoryGateway(Protocol):
    """Stream bounded history pages without exposing SDK pagination tokens."""

    def iter_history_pages(
        self,
        query: TelegramHistoryQuery,
    ) -> AsyncIterator[TelegramHistoryPage]:
        """Yield mapped pages inside an explicit UTC interval."""
        ...


@runtime_checkable
class TelegramLiveSubscription(Protocol):
    """Consume one bounded live stream and release its SDK handler."""

    def __aiter__(self) -> AsyncIterator[TelegramTextMessage]:
        """Return the live message iterator."""
        ...

    async def __anext__(self) -> TelegramTextMessage:
        """Return the next mapped event or raise a safe boundary failure."""
        ...

    async def close(self) -> None:
        """Unsubscribe and release all owned stream resources idempotently."""
        ...


@runtime_checkable
class TelegramLiveGateway(Protocol):
    """Create bounded subscriptions for one canonical source."""

    async def subscribe(
        self,
        source_channel_id: int,
        *,
        buffer_size: int,
    ) -> TelegramLiveSubscription:
        """Subscribe without embedding persistence or business logic."""
        ...


@runtime_checkable
class TelegramSourceGateway(
    TelegramAuthenticationGateway,
    TelegramValidationGateway,
    TelegramHistoryGateway,
    TelegramLiveGateway,
    Protocol,
):
    """Combine the owned Telegram source contracts for concrete adapters."""


__all__ = (
    "ResolvedTelegramChannel",
    "TelegramAccount",
    "TelegramAuthenticationGateway",
    "TelegramChannelNotFoundError",
    "TelegramChannelPermissionError",
    "TelegramChannelReference",
    "TelegramChannelRole",
    "TelegramHistoryGateway",
    "TelegramHistoryPage",
    "TelegramHistoryQuery",
    "TelegramInvalidCodeError",
    "TelegramInvalidPasswordError",
    "TelegramLiveGateway",
    "TelegramLiveSubscription",
    "TelegramLoginStep",
    "TelegramOperationTimeoutError",
    "TelegramRateLimitError",
    "TelegramSessionInvalidError",
    "TelegramSessionMutationConflictError",
    "TelegramSessionStatus",
    "TelegramSourceGateway",
    "TelegramTextMessage",
    "TelegramTransientError",
    "TelegramValidationGateway",
)
