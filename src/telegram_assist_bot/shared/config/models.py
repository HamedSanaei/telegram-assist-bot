"""Immutable typed models for application configuration documents."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    AnyHttpUrl,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    StringConstraints,
)

SUPPORTED_CONFIGURATION_SCHEMA_VERSION: Final[int] = 1
"""The only configuration schema version supported by this release."""


def _require_non_blank(value: str) -> str:
    """Reject blank text without normalizing the original value."""
    if value.isspace():
        raise ValueError("must not be blank")
    return value


def _require_non_zero(value: int) -> int:
    """Reject zero while preserving valid signed Telegram identifiers."""
    if value == 0:
        raise ValueError("must not be zero")
    return value


def _require_string_input(value: object) -> object:
    """Reject scalar coercion before an enum consumes its string value."""
    if not isinstance(value, str):
        raise ValueError("must be a string")
    return value


def _require_integer_input(value: object) -> object:
    """Reject booleans and numeric coercion for schema-version literals."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be an integer")
    return value


def _validate_local_path_input(value: object) -> object:
    """Reject blank or URL-shaped values before conversion to ``Path``."""
    if isinstance(value, str):
        if not value or value.isspace():
            raise ValueError("must not be blank")
        if "://" in value:
            raise ValueError("must be a local filesystem path, not a URL")
    return value


def _parse_timezone(value: object) -> ZoneInfo:
    """Convert an exact IANA timezone key into an immutable ``ZoneInfo``."""
    if isinstance(value, ZoneInfo):
        return value
    if not isinstance(value, str) or not value or value.isspace():
        raise ValueError("must be a non-blank IANA timezone name")
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as error:
        raise ValueError("must be a recognized IANA timezone name") from error


NonBlankString = Annotated[
    StrictStr,
    StringConstraints(min_length=1, max_length=256),
    AfterValidator(_require_non_blank),
]
"""A strict non-blank string that is never trimmed or normalized."""

EnvironmentVariableName = Annotated[
    StrictStr,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[A-Z_][A-Z0-9_]*$",
    ),
]
"""A portable uppercase environment-variable name."""

PositiveIdentifier = Annotated[StrictInt, Field(gt=0)]
"""A positive integer identifier that rejects booleans and numeric strings."""

TelegramEntityId = Annotated[StrictInt, AfterValidator(_require_non_zero)]
"""A non-zero signed Telegram peer identifier."""

LocalPath = Annotated[Path, BeforeValidator(_validate_local_path_input)]
"""A non-blank local path without canonicalization or filesystem access."""

ApplicationTimezone = Annotated[ZoneInfo, BeforeValidator(_parse_timezone)]
"""A validated IANA timezone represented by ``ZoneInfo``."""

ConfigurationSchemaVersion = Annotated[
    Literal[1], BeforeValidator(_require_integer_input)
]
"""The strict literal representation of configuration schema version one."""


class LogLevel(StrEnum):
    """Supported application logging thresholds."""

    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class AiTask(StrEnum):
    """AI task names that can be assigned an ordered route."""

    ADVERTISEMENT_DETECTION = "advertisement_detection"
    DUPLICATE_DETECTION = "duplicate_detection"
    CONTENT_SCORING = "content_scoring"


StrictLogLevel = Annotated[LogLevel, BeforeValidator(_require_string_input)]
"""A logging enum parsed only from an exact string scalar."""

StrictAiTask = Annotated[AiTask, BeforeValidator(_require_string_input)]
"""An AI task enum parsed only from an exact string scalar."""


class _FrozenConfigModel(BaseModel):
    """Apply the common immutable and closed-schema configuration policy."""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        hide_input_in_errors=True,
        validate_default=True,
    )


class SecretReference(_FrozenConfigModel):
    """Refer to a secret exclusively by its environment-variable name."""

    environment_variable: EnvironmentVariableName = Field(
        description="Environment variable that supplies the secret at startup."
    )


class MongoConfig(_FrozenConfigModel):
    """Describe MongoDB connection inputs without opening a connection."""

    uri: SecretReference = Field(
        description="Reference to a complete secret MongoDB connection URI."
    )
    database_name: NonBlankString = Field(
        description="MongoDB database used by the application."
    )
    connect_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=120)] = Field(
        description="Bounded MongoDB connection timeout in seconds."
    )


class TelegramUserConfig(_FrozenConfigModel):
    """Describe Telegram User API credentials and private session location."""

    api_id: SecretReference = Field(
        description="Reference to the positive Telegram API identifier."
    )
    api_hash: SecretReference = Field(description="Reference to the Telegram API hash.")
    phone_number: SecretReference = Field(
        description="Reference to the Telegram account phone number."
    )
    session_path: LocalPath = Field(
        description="Private local path used for the Telegram user session."
    )


class TelegramBotConfig(_FrozenConfigModel):
    """Describe Bot API credentials and the administrator approval chat."""

    token: SecretReference = Field(
        description="Reference to the Telegram Bot API token."
    )
    approval_chat_id: TelegramEntityId = Field(
        description="Telegram chat or channel receiving approval messages."
    )


class TelegramConfig(_FrozenConfigModel):
    """Group the separate Telegram User API and Bot API settings."""

    user: TelegramUserConfig = Field(description="Telegram User API configuration.")
    bot: TelegramBotConfig = Field(description="Telegram Bot API configuration.")


class AdminConfig(_FrozenConfigModel):
    """Authorize one administrator for an explicit set of destinations."""

    name: NonBlankString = Field(
        description="Stable human-readable administrator name."
    )
    telegram_user_id: PositiveIdentifier = Field(
        description="Positive Telegram user identifier for authorization."
    )
    active: StrictBool = Field(
        description="Whether this administrator is currently authorized."
    )
    allowed_destination_names: Annotated[
        tuple[NonBlankString, ...], Field(min_length=1)
    ] = Field(description="Destination names this administrator may operate on.")


class SourceChannelConfig(_FrozenConfigModel):
    """Describe one source channel and its permitted destination routes."""

    name: NonBlankString = Field(description="Stable source-channel name.")
    telegram_channel_id: TelegramEntityId = Field(
        description="Non-zero Telegram identifier of the source channel."
    )
    enabled: StrictBool = Field(
        description="Whether collection from this source channel is enabled."
    )
    allowed_destination_names: Annotated[
        tuple[NonBlankString, ...], Field(min_length=1)
    ] = Field(description="Destination names permitted for collected posts.")


class DestinationChannelConfig(_FrozenConfigModel):
    """Describe one channel that may receive approved publications."""

    name: NonBlankString = Field(description="Stable destination-channel name.")
    telegram_channel_id: TelegramEntityId = Field(
        description="Non-zero Telegram identifier of the destination channel."
    )
    enabled: StrictBool = Field(
        description="Whether publication to this destination is enabled."
    )


class FeatureFlags(_FrozenConfigModel):
    """Control the three configurable content-processing capabilities."""

    advertisement_detection_enabled: StrictBool = Field(
        description="Whether advertisement detection is enabled."
    )
    duplicate_detection_enabled: StrictBool = Field(
        description="Whether duplicate-content detection is enabled."
    )
    ai_scoring_enabled: StrictBool = Field(
        description="Whether AI content scoring is enabled."
    )


class PublishingConfig(_FrozenConfigModel):
    """Hold the base interval for scheduled publication."""

    scheduled_publication_interval_seconds: Annotated[StrictInt, Field(gt=0)] = Field(
        description="Positive interval between scheduled posts in seconds."
    )


class LoggingConfig(_FrozenConfigModel):
    """Hold the minimum logging configuration required at startup."""

    level: StrictLogLevel = Field(description="Minimum application logging level.")


class AiProviderConfig(_FrozenConfigModel):
    """Register an AI provider without selecting or contacting it."""

    name: NonBlankString = Field(description="Stable AI provider name.")
    enabled: StrictBool = Field(description="Whether routes may select this provider.")
    api_key: SecretReference | None = Field(
        default=None,
        description="Optional API-key reference; enabled providers require one.",
    )
    base_url: AnyHttpUrl | None = Field(
        default=None,
        description="Optional HTTP or HTTPS base URL for a provider adapter.",
    )


class AiRouteCandidateConfig(_FrozenConfigModel):
    """Describe one bounded model attempt in an ordered AI route."""

    provider_name: NonBlankString = Field(
        description="Name of the referenced AI provider."
    )
    model_name: NonBlankString = Field(description="Provider-owned model identifier.")
    priority: Annotated[StrictInt, Field(ge=0)] = Field(
        description="Ascending candidate priority within the task route."
    )
    timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = Field(
        description="Bounded timeout for each provider attempt in seconds."
    )
    max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = Field(
        description="Bounded total attempts, including the initial attempt."
    )


class AiTaskRouteConfig(_FrozenConfigModel):
    """Associate one AI task with a non-empty ordered candidate sequence."""

    task: StrictAiTask = Field(description="AI task handled by this route.")
    candidates: Annotated[tuple[AiRouteCandidateConfig, ...], Field(min_length=1)] = (
        Field(description="Ordered fallback candidates for the task.")
    )


class AiConfig(_FrozenConfigModel):
    """Hold provider declarations and task-routing skeletons."""

    providers: tuple[AiProviderConfig, ...] = Field(
        description="Declared AI providers; may be empty when AI features are off."
    )
    routes: tuple[AiTaskRouteConfig, ...] = Field(
        description="Configured AI task routes."
    )


class AdvertisementRouteConfig(_FrozenConfigModel):
    """Name a basic advertisement route and its permitted destinations."""

    name: NonBlankString = Field(description="Stable advertisement-route name.")
    destination_names: Annotated[tuple[NonBlankString, ...], Field(min_length=1)] = (
        Field(description="Destination names included in this route.")
    )


class AdvertisementConfig(_FrozenConfigModel):
    """Hold the initial advertisement-routing skeleton only."""

    routes: tuple[AdvertisementRouteConfig, ...] = Field(
        description="Configured advertisement routes."
    )


class ApplicationConfig(_FrozenConfigModel):
    """Represent the complete immutable version-one configuration document."""

    configuration_schema_version: ConfigurationSchemaVersion = Field(
        description="Configuration schema version; only version 1 is supported."
    )
    mongodb: MongoConfig = Field(description="MongoDB configuration.")
    telegram: TelegramConfig = Field(description="Telegram client configuration.")
    admins: Annotated[tuple[AdminConfig, ...], Field(min_length=1)] = Field(
        description="Authorized Telegram administrators."
    )
    source_channels: Annotated[tuple[SourceChannelConfig, ...], Field(min_length=1)] = (
        Field(description="Configured public source channels.")
    )
    destination_channels: Annotated[
        tuple[DestinationChannelConfig, ...], Field(min_length=1)
    ] = Field(description="Configured publication destination channels.")
    features: FeatureFlags = Field(description="Content-processing feature flags.")
    publishing: PublishingConfig = Field(
        description="Scheduled-publication configuration."
    )
    timezone: ApplicationTimezone = Field(
        description="Validated application IANA timezone."
    )
    logging: LoggingConfig = Field(description="Application logging configuration.")
    ai: AiConfig = Field(description="AI provider and routing configuration.")
    advertisements: AdvertisementConfig = Field(
        description="Advertisement routing configuration."
    )


__all__ = [
    "SUPPORTED_CONFIGURATION_SCHEMA_VERSION",
    "AdminConfig",
    "AdvertisementConfig",
    "AdvertisementRouteConfig",
    "AiConfig",
    "AiProviderConfig",
    "AiRouteCandidateConfig",
    "AiTask",
    "AiTaskRouteConfig",
    "ApplicationConfig",
    "DestinationChannelConfig",
    "FeatureFlags",
    "LogLevel",
    "LoggingConfig",
    "MongoConfig",
    "PublishingConfig",
    "SecretReference",
    "SourceChannelConfig",
    "TelegramBotConfig",
    "TelegramConfig",
    "TelegramUserConfig",
]
