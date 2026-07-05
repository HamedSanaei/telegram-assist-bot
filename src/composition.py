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
from src.infrastructure.ai.openai_compatible import OpenAiCompatibleProvider
from src.infrastructure.db.mongo.post_repository import MongoPostRepository
from src.infrastructure.db.sqlite.connection import Database
from src.infrastructure.db.sqlite.migrations import apply_migrations
from src.infrastructure.price.http_price_source import HttpJsonPriceSource
from src.infrastructure.price.nobitex_price_source import NobitexPriceSource
from src.domain.interfaces import PriceSource
from src.infrastructure.db.sqlite.repositories import (
    SqliteAdminRepository,
    SqliteApprovalMessageRepository,
    SqliteApprovalRequestRepository,
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


def create_ai_service(config: AppConfig) -> AiService:
    """
    Build the AI service with the configured provider chain.

    Args:
        config: Loaded application configuration.

    Returns:
        The configured :class:`AiService`.

    Raises:
        ConfigurationError: When no enabled provider has enough settings.
    """
    providers = [
        OpenAiCompatibleProvider(
            name=entry.name,
            api_key=entry.api_key,
            base_url=entry.base_url,
            default_model=entry.model,
            classification_model=entry.model,
            deduplication_model=entry.model,
            timeout_seconds=entry.timeout_seconds,
        )
        for entry in config.ai.providers
        if entry.enabled and entry.api_key and entry.base_url and entry.model
    ]
    if not providers:
        raise ConfigurationError(
            "No enabled AI provider has api_key, base_url, and model configured"
        )
    return AiService(providers)


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
    Mirror runtime channel/admin lists from ``configuration.json`` into SQLite.

    Source channels, destination channels, and admin ids in the config file
    are authoritative at startup and during hot reload. Rows missing from
    the config are disabled (channels) or removed (admins). Secrets and
    other settings remain restart-only.

    Args:
        config: Loaded application configuration.
        db: Connected SQLite database.
    """
    channels = SqliteChannelRepository(db)
    admins = SqliteAdminRepository(db)
    approval_messages = SqliteApprovalMessageRepository(db)
    destination_chat_ids: set[int] = set()
    for entry in config.telegram.destination_channels:
        destination_chat_ids.add(entry.chat_id)
        await channels.seed_destination(
            DestinationChannel(
                chat_id=entry.chat_id,
                title=entry.title,
                public_id=entry.public_id,
                kind=ChannelKind(entry.kind),
                publish_usd_price=entry.publish_usd_price,
                post_interval_minutes=entry.post_interval_minutes,
            )
        )
        await channels.upsert_destination(
            DestinationChannel(
                chat_id=entry.chat_id,
                title=entry.title,
                public_id=entry.public_id,
                kind=ChannelKind(entry.kind),
                publish_usd_price=entry.publish_usd_price,
                post_interval_minutes=entry.post_interval_minutes,
            )
        )
    disabled_destinations = await channels.disable_destinations_except(
        destination_chat_ids
    )
    source_identifiers = {str(identifier) for identifier in config.telegram.source_channels}
    for identifier in config.telegram.source_channels:
        await channels.upsert_source(str(identifier))
    disabled_sources = await channels.disable_sources_except(source_identifiers)
    admin_users = [
        AdminUser(telegram_user_id=user_id) for user_id in config.telegram.admin_user_ids
    ]
    await admins.replace_all(admin_users)
    deactivated_approval_messages = await approval_messages.deactivate_admins_except(
        {admin.telegram_user_id for admin in admin_users}
    )
    logger.info(
        "Synced config into SQLite destinations=%d sources=%d admins=%d "
        "disabled_destinations=%d disabled_sources=%d "
        "deactivated_approval_messages=%d",
        len(config.telegram.destination_channels),
        len(config.telegram.source_channels),
        len(config.telegram.admin_user_ids),
        disabled_destinations,
        disabled_sources,
        deactivated_approval_messages,
    )


def create_repositories(db: Database) -> dict[str, object]:
    """
    Build all SQLite repositories.

    Args:
        db: Connected SQLite database.

    Returns:
        Mapping with keys ``channels``, ``admins``, ``queue``,
        ``approval_requests``, ``publish_log``, and ``price_history``.
    """
    return {
        "channels": SqliteChannelRepository(db),
        "admins": SqliteAdminRepository(db),
        "queue": SqliteQueueRepository(db),
        "approval_requests": SqliteApprovalRequestRepository(db),
        "approval_messages": SqliteApprovalMessageRepository(db),
        "publish_log": SqlitePublishLogRepository(db),
        "price_history": SqlitePriceHistoryRepository(db),
    }
