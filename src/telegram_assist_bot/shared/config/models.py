"""Immutable typed models for application configuration documents."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, ClassVar, Final, Literal, Self
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
    model_validator,
)

from telegram_assist_bot.application.ai.contracts import AITaskType

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


def _map_ai_task_alias(value: object) -> object:
    if isinstance(value, str):
        if value == "duplicate_detection":
            return "semantic_duplicate"
        if value == "content_scoring":
            return "scoring"
    return value


StrictLogLevel = Annotated[LogLevel, BeforeValidator(_require_string_input)]
"""A logging enum parsed only from an exact string scalar."""

StrictAiTask = Annotated[
    AITaskType,
    BeforeValidator(_map_ai_task_alias),
    BeforeValidator(_require_string_input),
]
"""A strict AI task enum that maps the two supported legacy aliases."""


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
    operation_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=120)] = 10
    polling_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=60)] = 30
    approval_delivery_poll_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 5
    approval_delivery_interval_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 1
    approval_delivery_batch_pause_seconds: Annotated[
        StrictInt, Field(ge=1, le=3600)
    ] = 10
    approval_delivery_max_per_startup: Annotated[StrictInt, Field(ge=1, le=1000)] = 10
    approval_media_upload_timeout_seconds: Annotated[
        StrictInt, Field(ge=1, le=3600)
    ] = 300
    approval_claim_lease_seconds: Annotated[StrictInt, Field(ge=10, le=900)] = 60
    approval_retry_max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3
    shutdown_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 10


class TelegramIngestionConfig(_FrozenConfigModel):
    """Bound history, live buffering, reconnect, and FloodWait behavior."""

    operation_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=120)] = 10
    history_page_size: Annotated[StrictInt, Field(ge=1, le=1000)] = 100
    history_max_pages: Annotated[StrictInt, Field(ge=1, le=1000)] = 100
    live_buffer_size: Annotated[StrictInt, Field(ge=1, le=10_000)] = 100
    max_reconnect_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3
    reconnect_initial_delay_seconds: Annotated[
        StrictInt,
        Field(ge=0, le=300),
    ] = 1
    reconnect_max_delay_seconds: Annotated[StrictInt, Field(ge=0, le=600)] = 30
    maximum_flood_wait_seconds: Annotated[StrictInt, Field(ge=0, le=3600)] = 60


class TelegramConfig(_FrozenConfigModel):
    """Group the separate Telegram User API and Bot API settings."""

    user: TelegramUserConfig = Field(description="Telegram User API configuration.")
    bot: TelegramBotConfig = Field(description="Telegram Bot API configuration.")
    ingestion: TelegramIngestionConfig = Field(
        default_factory=TelegramIngestionConfig,
        description="Bounded Telegram text-ingestion behavior.",
    )


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
    role: Literal["admin"] = "admin"
    permissions: tuple[Literal["approval.view", "approval.toggle"], ...] = (
        "approval.view",
        "approval.toggle",
    )
    allowed_destination_names: Annotated[
        tuple[NonBlankString, ...], Field(min_length=1)
    ] = Field(description="Destination names this administrator may operate on.")
    allowed_destination_ids: tuple[TelegramEntityId, ...] = ()


class SourceChannelConfig(_FrozenConfigModel):
    """Describe one source channel and its permitted destination routes."""

    name: NonBlankString = Field(description="Stable source-channel name.")
    username: NonBlankString = Field(
        description="Public Telegram username used to resolve this source channel."
    )
    telegram_channel_id: TelegramEntityId | None = Field(
        default=None,
        description=(
            "Optional legacy source identifier. When omitted, startup resolves "
            "the canonical identifier from username."
        ),
    )
    enabled: StrictBool = Field(
        description="Whether collection from this source channel is enabled."
    )
    allowed_destination_names: Annotated[
        tuple[NonBlankString, ...], Field(min_length=1)
    ] = Field(description="Destination names permitted for collected posts.")
    default_category_id: NonBlankString | None = Field(
        default=None, description="Stable default category for baseline categorization."
    )


class DestinationChannelConfig(_FrozenConfigModel):
    """Describe one channel that may receive approved publications."""

    name: NonBlankString = Field(description="Stable destination-channel name.")
    telegram_channel_id: TelegramEntityId = Field(
        description="Non-zero Telegram identifier of the destination channel."
    )
    username: NonBlankString | None = Field(
        default=None,
        description="Optional public Telegram username used during resolution.",
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
    """Configure bounded immediate publication and persistent scheduling."""

    scheduled_publication_interval_seconds: Annotated[
        StrictInt, Field(ge=1, le=86_400)
    ] = Field(description="Positive interval between scheduled posts in seconds.")
    operation_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 30
    publication_lease_seconds: Annotated[StrictInt, Field(ge=1, le=900)] = 60
    publication_max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3
    retry_initial_delay_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 1
    retry_maximum_delay_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 30
    worker_poll_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 5
    shutdown_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 10
    cancellation_policy: Literal["preserve", "recompact"] = "preserve"
    native_schedule_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=600)] = 300
    native_schedule_lease_seconds: Annotated[StrictInt, Field(ge=2, le=900)] = 600
    native_schedule_poll_seconds: Annotated[StrictInt, Field(ge=1, le=30)] = 1

    @model_validator(mode="after")
    def validate_publication_bounds(self) -> Self:
        """Require leases and delay caps to cover their subordinate operation."""
        if self.publication_lease_seconds < self.operation_timeout_seconds:
            raise ValueError("Publication lease must cover the operation timeout.")
        if self.retry_maximum_delay_seconds < self.retry_initial_delay_seconds:
            raise ValueError("Publication retry delay cap is invalid.")
        if self.native_schedule_lease_seconds <= self.native_schedule_timeout_seconds:
            raise ValueError(
                "Native scheduling lease must exceed the operation timeout."
            )
        return self


class LoggingConfig(_FrozenConfigModel):
    """Hold the minimum logging configuration required at startup."""

    level: StrictLogLevel = Field(description="Minimum application logging level.")


class MediaStorageConfig(_FrozenConfigModel):
    """Configure bounded private local media handling."""

    root: Path = Field(default=Path("var/media"))
    preview_enabled: StrictBool = False
    maximum_bytes: Annotated[StrictInt, Field(ge=1, le=2_147_483_648)] = 104_857_600
    download_timeout_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 300
    download_max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3
    cleanup_batch_size: Annotated[StrictInt, Field(ge=1, le=1000)] = 100
    orphan_grace_seconds: Annotated[StrictInt, Field(ge=60, le=604800)] = 3600
    album_quiet_seconds: Annotated[StrictInt, Field(ge=1, le=300)] = 3
    album_maximum_wait_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 30
    album_finalization_retry_seconds: Annotated[StrictInt, Field(ge=1, le=3600)] = 5
    album_finalization_lease_seconds: Annotated[StrictInt, Field(ge=5, le=3600)] = 300
    album_finalization_max_attempts: Annotated[StrictInt, Field(ge=1, le=10)] = 3


class CategoryConfig(_FrozenConfigModel):
    """Configure one stable category identity and display label."""

    category_id: NonBlankString
    display_name: NonBlankString


class CategoryKeywordRuleConfig(_FrozenConfigModel):
    """Configure one deterministic bounded keyword rule."""

    rule_id: NonBlankString
    category_id: NonBlankString
    keyword: Annotated[NonBlankString, Field(max_length=128)]
    priority: Annotated[StrictInt, Field(ge=0, le=10000)]


class CategorizationConfig(_FrozenConfigModel):
    """Hold the baseline category catalog and keyword rules."""

    categories: tuple[CategoryConfig, ...] = ()
    keyword_rules: tuple[CategoryKeywordRuleConfig, ...] = ()


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


class AiProviderGuardConfig(_FrozenConfigModel):
    """Explicit request-capacity and circuit policy for one route candidate."""

    concurrency_limit: Annotated[StrictInt, Field(ge=1, le=1000)]
    request_limit: Annotated[StrictInt, Field(ge=1, le=1_000_000)]
    request_window_seconds: Annotated[StrictInt, Field(ge=1, le=86400)]
    reservation_seconds: Annotated[StrictInt, Field(ge=1, le=3600)]
    failure_threshold: Annotated[StrictInt, Field(ge=1, le=1000)]
    open_seconds: Annotated[StrictInt, Field(ge=1, le=86400)]
    rate_limit_cooldown_seconds: Annotated[StrictInt, Field(ge=1, le=86400)] | None


class AiCachePolicyConfig(_FrozenConfigModel):
    """Explicit cache enablement and bounded TTL for one canonical AI task."""

    task: StrictAiTask
    enabled: StrictBool = False
    ttl_seconds: Annotated[StrictInt, Field(ge=1, le=31_536_000)] | None = None

    @model_validator(mode="after")
    def validate_enabled_ttl(self) -> Self:
        """Require an explicit TTL only when caching is enabled."""
        if self.enabled and self.ttl_seconds is None:
            raise ValueError("enabled AI cache policy requires ttl_seconds")
        return self


class AiAuditConfig(_FrozenConfigModel):
    """Disabled-by-default sanitized audit retention policy."""

    enabled: StrictBool = False
    retention_seconds: Annotated[StrictInt, Field(ge=1, le=31_536_000)] | None = None
    raw_storage_enabled: Literal[False] = False

    @model_validator(mode="after")
    def validate_enabled_retention(self) -> Self:
        """Require explicit bounded retention before audit persistence is enabled."""
        if self.enabled and self.retention_seconds is None:
            raise ValueError("enabled AI audit requires retention_seconds")
        return self


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
    guard_policy: AiProviderGuardConfig | None = Field(
        default=None,
        description=(
            "Explicit T040 guard policy; required before an enabled candidate executes."
        ),
    )


class AiTaskRouteConfig(_FrozenConfigModel):
    """Associate one AI task with a non-empty ordered candidate sequence."""

    task: StrictAiTask = Field(description="AI task handled by this route.")
    candidates: Annotated[tuple[AiRouteCandidateConfig, ...], Field(min_length=1)] = (
        Field(description="Ordered fallback candidates for the task.")
    )


class AiQueueConfig(_FrozenConfigModel):
    """Hold AI Job queue parameters."""

    lease_duration_seconds: int = Field(
        default=60,
        ge=1,
        le=86400,
        description="Lease duration in seconds for claimed jobs.",
    )
    max_attempts: int = Field(
        default=3,
        ge=1,
        le=100,
        description="Maximum attempts to process a job across fallbacks.",
    )
    next_run_delay_seconds: int = Field(
        default=30,
        ge=1,
        le=86400,
        description="Delay in seconds before retrying a failed job attempt.",
    )
    worker_poll_seconds: int = Field(
        default=5,
        ge=1,
        le=3600,
        description="Polling interval in seconds for workers.",
    )


class AiTaskFailureAction(StrEnum):
    """Actions to take on final all-providers failure."""

    CONTINUE = "continue"
    STOP = "stop"
    MANUAL_REVIEW = "manual_review"
    DEFAULT_CATEGORY = "default_category"


class AiTaskFailurePolicyConfig(_FrozenConfigModel):
    """Define failure policy for a task."""

    task: StrictAiTask = Field(description="AI task this failure policy applies to.")
    action: AiTaskFailureAction = Field(
        description="Action to take when all providers fail."
    )


class AiConfig(_FrozenConfigModel):
    """Hold provider declarations and task-routing skeletons."""

    queue: AiQueueConfig = Field(
        default_factory=AiQueueConfig,
        description="Optional AI job queue configuration.",
    )
    providers: tuple[AiProviderConfig, ...] = Field(
        description="Declared AI providers; may be empty when AI features are off."
    )
    routes: tuple[AiTaskRouteConfig, ...] = Field(
        description="Configured AI task routes."
    )
    failure_policies: tuple[AiTaskFailurePolicyConfig, ...] = Field(
        default=(), description="Configured AI task failure policies."
    )
    cache_policies: tuple[AiCachePolicyConfig, ...] = Field(
        default=(), description="Explicit per-task result-cache policies."
    )
    audit: AiAuditConfig = Field(
        default_factory=AiAuditConfig,
        description="Sanitized audit retention; raw storage remains disabled.",
    )

    @model_validator(mode="after")
    def validate_unique_cache_tasks(self) -> Self:
        """Reject ambiguous duplicate cache policies after alias canonicalization."""
        tasks = [policy.task for policy in self.cache_policies]
        if len(tasks) != len(set(tasks)):
            raise ValueError("AI cache policy tasks must be unique")
        return self


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
    media: MediaStorageConfig = Field(default_factory=MediaStorageConfig)
    categorization: CategorizationConfig = Field(default_factory=CategorizationConfig)
    ai: AiConfig = Field(description="AI provider and routing configuration.")
    advertisements: AdvertisementConfig = Field(
        description="Advertisement routing configuration."
    )

    @model_validator(mode="after")
    def validate_milestone_two_references(self) -> ApplicationConfig:
        """Validate category identities, rules and source defaults deterministically."""
        category_ids = [item.category_id for item in self.categorization.categories]
        rule_ids = [item.rule_id for item in self.categorization.keyword_rules]
        if len(category_ids) != len(set(category_ids)):
            raise ValueError("categorization category_id values must be unique")
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("categorization rule_id values must be unique")
        known = set(category_ids)
        if any(
            rule.category_id not in known for rule in self.categorization.keyword_rules
        ):
            raise ValueError(
                "categorization keyword rule references an unknown category"
            )
        defaults = [
            source.default_category_id
            for source in self.source_channels
            if source.default_category_id is not None
        ]
        if any(value not in known for value in defaults):
            raise ValueError(
                "source default_category_id references an unknown category"
            )
        destination_ids = {
            item.telegram_channel_id for item in self.destination_channels
        }
        destination_by_name = {
            item.name: item.telegram_channel_id for item in self.destination_channels
        }
        for admin in self.admins:
            if len(admin.permissions) != len(set(admin.permissions)):
                raise ValueError("administrator permissions must be unique")
            configured = set(admin.allowed_destination_ids)
            if configured and not configured <= destination_ids:
                raise ValueError("administrator references an unknown destination id")
            effective = configured or {
                destination_by_name[name]
                for name in admin.allowed_destination_names
                if name in destination_by_name
            }
            if len(effective) > 20:
                raise ValueError(
                    "an administrator may access at most 20 active destinations"
                )
        return self


__all__ = [
    "SUPPORTED_CONFIGURATION_SCHEMA_VERSION",
    "AdminConfig",
    "AdvertisementConfig",
    "AdvertisementRouteConfig",
    "AiAuditConfig",
    "AiCachePolicyConfig",
    "AiConfig",
    "AiProviderConfig",
    "AiProviderGuardConfig",
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
    "TelegramIngestionConfig",
    "TelegramUserConfig",
]
