"""Configuration loading and validation.

Sensitive values live in ``config/configuration.json`` (never committed).
This module parses that file into typed, frozen dataclasses and validates
structure. Emptiness of secrets is validated per entrypoint via the
``validate_*`` helpers, because the main bot, the collector, and the Iran
VPN worker each need a different subset of secrets.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.shared.errors import ConfigurationError

DEFAULT_CONFIG_PATH = "config/configuration.json"
CONFIG_PATH_ENV_VAR = "TELEGRAM_ADMIN_BOT_CONFIG"

DEFAULT_ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"


@dataclass(frozen=True)
class DestinationChannelConfig:
    """A destination Telegram channel the system may publish to."""

    chat_id: int
    title: str
    kind: str = "news"
    publish_usd_price: bool = False


@dataclass(frozen=True)
class TelegramConfig:
    """Telegram credentials, channels, and admin user IDs."""

    bot_token: str
    approval_bot_token: str
    api_id: str
    api_hash: str
    source_channels: list[str | int] = field(default_factory=list)
    destination_channels: list[DestinationChannelConfig] = field(default_factory=list)
    admin_user_ids: list[int] = field(default_factory=list)
    collector_session: str = "data/collector"
    collector_startup_backfill_limit: int = 10


@dataclass(frozen=True)
class AiConfig:
    """AI provider selection, credentials, and model overrides."""

    primary_provider: str = "zai"
    fallback_provider: str = "deepseek"
    zai_api_key: str = ""
    deepseek_api_key: str = ""
    zai_base_url: str = DEFAULT_ZAI_BASE_URL
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    deduplication_model: str = ""
    classification_model: str = ""
    request_timeout_seconds: int = 30
    recent_posts_compare_limit: int = 30


@dataclass(frozen=True)
class DatabaseConfig:
    """SQLite and MongoDB connection settings."""

    sqlite_path: str = "data/app.db"
    mongodb_connection_string: str = ""
    mongodb_database: str = "telegram_admin_bot"


@dataclass(frozen=True)
class StorageConfig:
    """Media storage location and post retention policy."""

    media_directory: str = "data/media"
    retention_days: int = 14


@dataclass(frozen=True)
class VpnTestingConfig:
    """Settings for the Iran-based VPN connectivity testing worker."""

    iran_worker_enabled: bool = True
    worker_api_url: str = ""
    worker_api_token: str = ""
    test_timeout_seconds: int = 30
    worker_listen_host: str = "0.0.0.0"
    worker_listen_port: int = 8088
    xray_binary_path: str = ""
    test_url: str = "https://www.gstatic.com/generate_204"


@dataclass(frozen=True)
class SchedulerConfig:
    """Scheduled job times, expressed in the configured timezone."""

    usd_price_publish_times: list[str] = field(default_factory=lambda: ["09:00", "21:00"])
    timezone: str = "Asia/Tehran"
    cleanup_time: str = "04:30"


@dataclass(frozen=True)
class UsdPriceConfig:
    """
    USD price source settings.

    ``provider`` selects the implementation: ``"nobitex"`` (default; the
    public Nobitex market-stats API, no key needed) or ``"http_json"``
    (a generic JSON endpoint described by ``source_url`` and
    ``price_json_path``).
    """

    provider: str = "nobitex"
    source_name: str = ""
    source_url: str = ""
    price_json_path: str = ""
    request_timeout_seconds: int = 20


@dataclass(frozen=True)
class LoggingConfig:
    """Logging level and file destination."""

    level: str = "INFO"
    file: str = "logs/app.log"


@dataclass(frozen=True)
class AppConfig:
    """Root configuration object for the whole application."""

    telegram: TelegramConfig
    ai: AiConfig
    database: DatabaseConfig
    storage: StorageConfig
    vpn_testing: VpnTestingConfig
    scheduler: SchedulerConfig
    usd_price: UsdPriceConfig
    logging: LoggingConfig


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a required top-level configuration section or raise."""
    value = data.get(name)
    if not isinstance(value, dict):
        raise ConfigurationError(f"Missing or invalid configuration section: '{name}'")
    return value


def _parse_destinations(raw: list[Any]) -> list[DestinationChannelConfig]:
    """Parse the destination channel list, validating each entry."""
    channels: list[DestinationChannelConfig] = []
    for entry in raw:
        if not isinstance(entry, dict) or "chat_id" not in entry:
            raise ConfigurationError(
                "Each destination channel must be an object with at least 'chat_id'"
            )
        channels.append(
            DestinationChannelConfig(
                chat_id=int(entry["chat_id"]),
                title=str(entry.get("title", str(entry["chat_id"]))),
                kind=str(entry.get("kind", "news")),
                publish_usd_price=bool(entry.get("publish_usd_price", False)),
            )
        )
    return channels


def load_configuration(path: str | Path | None = None) -> AppConfig:
    """
    Load and parse the application configuration file.

    Args:
        path:
            Path to the JSON configuration file. When omitted, the
            ``TELEGRAM_ADMIN_BOT_CONFIG`` environment variable is used,
            falling back to ``config/configuration.json``.

    Returns:
        A fully populated :class:`AppConfig`.

    Raises:
        ConfigurationError:
            When the file is missing, not valid JSON, not UTF-8, or a
            required section/field is absent or has the wrong type.

    Example:
        config = load_configuration("config/configuration.json")
    """
    resolved = Path(path or os.environ.get(CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_PATH))
    if not resolved.exists():
        raise ConfigurationError(f"Configuration file not found: {resolved}")
    try:
        data = json.loads(resolved.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ConfigurationError(f"Configuration file is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigurationError("Configuration root must be a JSON object")

    tg = _section(data, "telegram")
    ai = _section(data, "ai")
    db = _section(data, "database")
    storage = data.get("storage", {})
    vpn = data.get("vpn_testing", {})
    sched = data.get("scheduler", {})
    usd = data.get("usd_price", {})
    log = data.get("logging", {})

    try:
        return AppConfig(
            telegram=TelegramConfig(
                bot_token=str(tg.get("bot_token", "")),
                approval_bot_token=str(tg.get("approval_bot_token", "")),
                api_id=str(tg.get("api_id", "")),
                api_hash=str(tg.get("api_hash", "")),
                source_channels=list(tg.get("source_channels", [])),
                destination_channels=_parse_destinations(list(tg.get("destination_channels", []))),
                admin_user_ids=[int(x) for x in tg.get("admin_user_ids", [])],
                collector_session=str(tg.get("collector_session", "data/collector")),
                collector_startup_backfill_limit=int(
                    tg.get("collector_startup_backfill_limit", 10)
                ),
            ),
            ai=AiConfig(
                primary_provider=str(ai.get("primary_provider", "zai")),
                fallback_provider=str(ai.get("fallback_provider", "deepseek")),
                zai_api_key=str(ai.get("zai_api_key", "")),
                deepseek_api_key=str(ai.get("deepseek_api_key", "")),
                zai_base_url=str(ai.get("zai_base_url", DEFAULT_ZAI_BASE_URL)),
                deepseek_base_url=str(ai.get("deepseek_base_url", DEFAULT_DEEPSEEK_BASE_URL)),
                deduplication_model=str(ai.get("deduplication_model", "")),
                classification_model=str(ai.get("classification_model", "")),
                request_timeout_seconds=int(ai.get("request_timeout_seconds", 30)),
                recent_posts_compare_limit=int(ai.get("recent_posts_compare_limit", 30)),
            ),
            database=DatabaseConfig(
                sqlite_path=str(db.get("sqlite_path", "data/app.db")),
                mongodb_connection_string=str(db.get("mongodb_connection_string", "")),
                mongodb_database=str(db.get("mongodb_database", "telegram_admin_bot")),
            ),
            storage=StorageConfig(
                media_directory=str(storage.get("media_directory", "data/media")),
                retention_days=int(storage.get("retention_days", 14)),
            ),
            vpn_testing=VpnTestingConfig(
                iran_worker_enabled=bool(vpn.get("iran_worker_enabled", True)),
                worker_api_url=str(vpn.get("worker_api_url", "")),
                worker_api_token=str(vpn.get("worker_api_token", "")),
                test_timeout_seconds=int(vpn.get("test_timeout_seconds", 30)),
                worker_listen_host=str(vpn.get("worker_listen_host", "0.0.0.0")),
                worker_listen_port=int(vpn.get("worker_listen_port", 8088)),
                xray_binary_path=str(vpn.get("xray_binary_path", "")),
                test_url=str(vpn.get("test_url", "https://www.gstatic.com/generate_204")),
            ),
            scheduler=SchedulerConfig(
                usd_price_publish_times=[str(t) for t in sched.get("usd_price_publish_times", ["09:00", "21:00"])],
                timezone=str(sched.get("timezone", "Asia/Tehran")),
                cleanup_time=str(sched.get("cleanup_time", "04:30")),
            ),
            usd_price=UsdPriceConfig(
                provider=str(usd.get("provider", "nobitex")),
                source_name=str(usd.get("source_name", "")),
                source_url=str(usd.get("source_url", "")),
                price_json_path=str(usd.get("price_json_path", "")),
                request_timeout_seconds=int(usd.get("request_timeout_seconds", 20)),
            ),
            logging=LoggingConfig(
                level=str(log.get("level", "INFO")),
                file=str(log.get("file", "logs/app.log")),
            ),
        )
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"Invalid configuration value: {exc}") from exc


def _require_non_empty(value: str, name: str) -> None:
    """Raise :class:`ConfigurationError` when a required value is empty."""
    if not value:
        raise ConfigurationError(f"Required configuration value is empty: {name}")


def validate_main_app_config(config: AppConfig) -> None:
    """
    Validate values required by the main bot process (``src.main``).

    Raises:
        ConfigurationError: When a required secret or setting is empty.
    """
    _require_non_empty(config.telegram.bot_token, "telegram.bot_token")
    _require_non_empty(config.telegram.approval_bot_token, "telegram.approval_bot_token")
    _require_non_empty(config.database.mongodb_connection_string, "database.mongodb_connection_string")
    if not config.telegram.admin_user_ids:
        raise ConfigurationError("telegram.admin_user_ids must contain at least one admin")
    if not config.telegram.destination_channels:
        raise ConfigurationError("telegram.destination_channels must not be empty")


def validate_collector_config(config: AppConfig) -> None:
    """
    Validate values required by the collector worker.

    Raises:
        ConfigurationError: When a required secret or setting is empty.
    """
    _require_non_empty(config.telegram.api_id, "telegram.api_id")
    _require_non_empty(config.telegram.api_hash, "telegram.api_hash")
    _require_non_empty(config.database.mongodb_connection_string, "database.mongodb_connection_string")
    if not config.telegram.source_channels:
        raise ConfigurationError("telegram.source_channels must not be empty")
    if config.telegram.collector_startup_backfill_limit < 0:
        raise ConfigurationError("telegram.collector_startup_backfill_limit must be >= 0")


def validate_worker_config(config: AppConfig) -> None:
    """
    Validate values required by the Iran VPN testing worker.

    Raises:
        ConfigurationError: When a required secret or setting is empty.
    """
    _require_non_empty(config.vpn_testing.worker_api_token, "vpn_testing.worker_api_token")
    _require_non_empty(config.vpn_testing.xray_binary_path, "vpn_testing.xray_binary_path")


def _mongo_host_only(connection_string: str) -> str:
    """Return the host part of a MongoDB URI, stripping any credentials."""
    if not connection_string:
        return "(not set)"
    without_scheme = connection_string.split("://", 1)[-1]
    host_part = without_scheme.rsplit("@", 1)[-1]
    return host_part.split("/", 1)[0]


def log_startup_summary(config: AppConfig) -> None:
    """
    Log a non-secret summary of the effective configuration.

    Logged once at process startup so misconfiguration (empty API keys,
    no source channels, wrong database) is visible immediately in the
    logs. Never logs tokens, keys, or connection credentials.

    Args:
        config: Loaded application configuration.

    Side effects:
        Writes one multi-line INFO log record.
    """
    from src.shared.logging_setup import get_logger

    def set_or_not(value: str) -> str:
        """Describe a secret as present or missing without revealing it."""
        return "set" if value else "EMPTY"

    ai = config.ai
    logger = get_logger(__name__)
    logger.info(
        "Effective configuration:\n"
        "  sources=%d destinations=%d admins=%d\n"
        "  ai: primary=%s (key %s) fallback=%s (key %s) "
        "classification_model=%s deduplication_model=%s\n"
        "  telegram: bot_token %s, approval_bot_token %s, api_id %s\n"
        "  mongodb: host=%s db=%s | sqlite: %s\n"
        "  vpn_testing: enabled=%s worker_url %s\n"
        "  usd_price: provider=%s publish_times=%s",
        len(config.telegram.source_channels),
        len(config.telegram.destination_channels),
        len(config.telegram.admin_user_ids),
        ai.primary_provider,
        set_or_not(ai.zai_api_key if ai.primary_provider == "zai" else ai.deepseek_api_key),
        ai.fallback_provider or "(none)",
        set_or_not(ai.deepseek_api_key if ai.fallback_provider == "deepseek" else ai.zai_api_key),
        ai.classification_model or "(provider default)",
        ai.deduplication_model or "(provider default)",
        set_or_not(config.telegram.bot_token),
        set_or_not(config.telegram.approval_bot_token),
        set_or_not(config.telegram.api_id),
        _mongo_host_only(config.database.mongodb_connection_string),
        config.database.mongodb_database,
        config.database.sqlite_path,
        config.vpn_testing.iran_worker_enabled,
        set_or_not(config.vpn_testing.worker_api_url),
        config.usd_price.provider,
        config.scheduler.usd_price_publish_times,
    )
