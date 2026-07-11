"""Create and verify bounded asynchronous MongoDB client connections."""

from __future__ import annotations

import asyncio
from datetime import UTC
from typing import TYPE_CHECKING, Final

from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError
from pymongo.server_api import ServerApi

from telegram_assist_bot.infrastructure.persistence.mongodb.errors import (
    MongoConnectionError,
)

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.shared.config import MongoConfig, ResolvedSecrets

type MongoDocument = dict[str, object]
"""The mutable document shape owned entirely by the MongoDB infrastructure."""

POSTS_COLLECTION_NAME: Final[str] = "posts"
"""The stable collection name for persisted post aggregates."""

MINIMUM_MONGODB_WIRE_VERSION: Final[int] = 21
"""MongoDB 7.0 wire version, the minimum supported deployment baseline."""


def create_mongodb_client(
    config: MongoConfig,
    secrets: ResolvedSecrets,
) -> AsyncMongoClient[MongoDocument]:
    """Build a lazy async client with one bounded deadline and no hidden retries."""
    timeout_ms = config.connect_timeout_seconds * 1000
    uri = secrets.get(config.uri).get_secret_value()
    client: AsyncMongoClient[MongoDocument] | None = None
    failed = False
    try:
        client = AsyncMongoClient[MongoDocument](
            uri,
            appname="telegram-assist-bot",
            connectTimeoutMS=timeout_ms,
            serverSelectionTimeoutMS=timeout_ms,
            socketTimeoutMS=timeout_ms,
            waitQueueTimeoutMS=timeout_ms,
            timeoutMS=timeout_ms,
            retryReads=False,
            retryWrites=False,
            server_api=ServerApi("1", strict=True, deprecation_errors=True),
            tz_aware=True,
            tzinfo=UTC,
        )
    except (PyMongoError, TypeError, ValueError):
        failed = True

    if failed or client is None:
        raise MongoConnectionError
    return client


def get_posts_collection(
    client: AsyncMongoClient[MongoDocument],
    config: MongoConfig,
) -> AsyncCollection[MongoDocument]:
    """Return the configured post collection without exposing it inward."""
    return client[config.database_name][POSTS_COLLECTION_NAME]


async def verify_mongodb_connection(
    client: AsyncMongoClient[MongoDocument],
    *,
    timeout_seconds: int,
) -> None:
    """Ping MongoDB and reject deployments older than the supported baseline."""
    reply: MongoDocument | None = None
    failed = False
    try:
        async with asyncio.timeout(timeout_seconds):
            await client.admin.command({"ping": 1})
            reply = await client.admin.command({"hello": 1})
    except (PyMongoError, TimeoutError, TypeError, ValueError):
        failed = True

    max_wire_version = None if reply is None else reply.get("maxWireVersion")
    if (
        failed
        or type(max_wire_version) is not int
        or max_wire_version < MINIMUM_MONGODB_WIRE_VERSION
    ):
        raise MongoConnectionError


async def close_mongodb_client(
    client: AsyncMongoClient[MongoDocument],
    *,
    timeout_seconds: int,
) -> None:
    """Close an async client within a bounded, credential-safe deadline."""
    failed = False
    try:
        async with asyncio.timeout(timeout_seconds):
            await client.close()
    except (PyMongoError, TimeoutError, TypeError, ValueError):
        failed = True
    if failed:
        raise MongoConnectionError


__all__ = (
    "MINIMUM_MONGODB_WIRE_VERSION",
    "POSTS_COLLECTION_NAME",
    "MongoDocument",
    "close_mongodb_client",
    "create_mongodb_client",
    "get_posts_collection",
    "verify_mongodb_connection",
)
