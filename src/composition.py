"""Composition root: builds and wires concrete implementations.

This is the only module (besides entrypoints) that knows about every
infrastructure implementation. Application services receive their
dependencies through constructor injection.
"""

from __future__ import annotations

from motor.motor_asyncio import AsyncIOMotorClient

from src.application.ai_service import AiService
from src.domain.entities import AdminUser, DestinationChannel
from src.domain.enums import ChannelKind
from src.infrastructure.ai.deepseek_provider import DeepSeekProvider
from src.infrastructure.ai.zai_provider import ZaiProvider
from src.infrastructure.db.mongo.post_repository import MongoPostRepository
from src.infrastructure.db.sqlite.connection import Database
from src.infrastructure.db.sqlite.migrations import apply_migrations
from src.infrastructure.price.http_price_source import HttpJsonPriceSource
from src.infrastructure.price.nobitex_price_source import NobitexPriceSource
from src.domain.interfaces import PriceSource
from src.infrastructure.db.sqlite.repositories import (
    SqliteAdminRepository,
    SqliteChannelRepository,
    SqlitePriceHistoryRepository,
    SqlitePublishLogRepository,
    SqliteQueueRepository,
)
from src.shared.config import AppConfig
from src.shared.errors import ConfigurationError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


async def create_sqlite(config: AppConfig) -> Database:
    """
    Open the SQLite database and apply pending migrations.

    Args:
        config: Loaded application configuration.

    Returns:
        A connected :class:`Database`.

    Raises:
        RepositoryError: When the database cannot be opened or migrated.
    """
    db = Database(config.database.sqlite_path)
    await db.connect()
    applied = await apply_migrations(db)
    if applied:
        logger.info("Applied %d SQLite migrations", applied)
    return db


def create_mongo(config: AppConfig) -> tuple[AsyncIOMotorClient, MongoPostRepository]:
    """
    Create the Motor client and the post repository.

    Args:
        config: Loaded application configuration.

    Returns:
        Tuple of ``(client, repository)``. Call
        ``repository.ensure_indexes()`` once after startup.
    """
    client = AsyncIOMotorClient(config.database.mongodb_connection_string)
    repo = MongoPostRepository(client[config.database.mongodb_database])
    return client, repo


def _build_provider(name: str, config: AppConfig) -> ZaiProvider | DeepSeekProvider:
    """
    Build one AI provider by configured name.

    Raises:
        ConfigurationError: When the provider name is unknown.
    """
    ai = config.ai
    if name == "zai":
        return ZaiProvider(
            api_key=ai.zai_api_key,
            base_url=ai.zai_base_url,
            classification_model=ai.classification_model,
            deduplication_model=ai.deduplication_model,
            timeout_seconds=ai.request_timeout_seconds,
        )
    if name == "deepseek":
        return DeepSeekProvider(
            api_key=ai.deepseek_api_key,
            base_url=ai.deepseek_base_url,
            classification_model=ai.classification_model,
            deduplication_model=ai.deduplication_model,
            timeout_seconds=ai.request_timeout_seconds,
        )
    raise ConfigurationError(f"Unknown AI provider: '{name}'")


def create_ai_service(config: AppConfig) -> AiService:
    """
    Build the AI service with primary and fallback providers.

    Args:
        config: Loaded application configuration.

    Returns:
        The configured :class:`AiService`.

    Raises:
        ConfigurationError: When a provider name is unknown.
    """
    primary = _build_provider(config.ai.primary_provider, config)
    fallback = (
        _build_provider(config.ai.fallback_provider, config)
        if config.ai.fallback_provider
        else None
    )
    return AiService(primary=primary, fallback=fallback)


def create_price_source(config: AppConfig) -> PriceSource:
    """
    Build the USD price source selected by ``usd_price.provider``.

    Args:
        config: Loaded application configuration.

    Returns:
        The configured :class:`PriceSource` implementation.

    Raises:
        ConfigurationError: When the provider name is unknown, or when
            the ``http_json`` provider is selected without ``source_url``
            and ``price_json_path``.
    """
    usd = config.usd_price
    if usd.provider == "nobitex":
        return NobitexPriceSource(timeout_seconds=usd.request_timeout_seconds)
    if usd.provider == "http_json":
        if not usd.source_url or not usd.price_json_path:
            raise ConfigurationError(
                "usd_price.provider 'http_json' requires usd_price.source_url "
                "and usd_price.price_json_path"
            )
        return HttpJsonPriceSource(
            name=usd.source_name or "usd",
            url=usd.source_url,
            price_json_path=usd.price_json_path,
            timeout_seconds=usd.request_timeout_seconds,
        )
    raise ConfigurationError(f"Unknown USD price provider: '{usd.provider}'")


async def sync_config_to_sqlite(config: AppConfig, db: Database) -> None:
    """
    Mirror channels and admins from ``configuration.json`` into SQLite.

    Runs on every startup so the configuration file stays the single
    source of truth while runtime reads hit SQLite.

    Args:
        config: Loaded application configuration.
        db: Connected SQLite database.
    """
    channels = SqliteChannelRepository(db)
    admins = SqliteAdminRepository(db)
    for entry in config.telegram.destination_channels:
        await channels.upsert_destination(
            DestinationChannel(
                chat_id=entry.chat_id,
                title=entry.title,
                public_id=entry.public_id,
                kind=ChannelKind(entry.kind),
                publish_usd_price=entry.publish_usd_price,
            )
        )
    for identifier in config.telegram.source_channels:
        await channels.upsert_source(str(identifier))
    for user_id in config.telegram.admin_user_ids:
        await admins.upsert(AdminUser(telegram_user_id=user_id))
    logger.info(
        "Synced config to SQLite destinations=%d sources=%d admins=%d",
        len(config.telegram.destination_channels),
        len(config.telegram.source_channels),
        len(config.telegram.admin_user_ids),
    )


def create_repositories(db: Database) -> dict[str, object]:
    """
    Build all SQLite repositories.

    Args:
        db: Connected SQLite database.

    Returns:
        Mapping with keys ``channels``, ``admins``, ``queue``,
        ``publish_log``, and ``price_history``.
    """
    return {
        "channels": SqliteChannelRepository(db),
        "admins": SqliteAdminRepository(db),
        "queue": SqliteQueueRepository(db),
        "publish_log": SqlitePublishLogRepository(db),
        "price_history": SqlitePriceHistoryRepository(db),
    }
