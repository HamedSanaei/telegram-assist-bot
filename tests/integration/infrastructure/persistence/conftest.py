"""Safe MongoDB settings and cleanup for T004 integration tests."""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from ipaddress import ip_address
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import PyMongoError

if TYPE_CHECKING:
    from collections.abc import Iterator, Mapping

_TEST_MONGODB_URI_VARIABLE = "TEST_MONGODB_URI"
_TEST_DATABASE_NAME_PATTERN = re.compile(r"tab_t004_[0-9a-f]{32}", re.ASCII)
_MONGODB_TIMEOUT_MILLISECONDS = 5_000


@dataclass(frozen=True, slots=True)
class MongoTestSettings:
    """Hold an isolated test database name without exposing its MongoDB URI."""

    uri: str = field(repr=False)
    database_name: str


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ip_address(host).is_loopback
    except ValueError:
        return False


def _read_test_mongodb_uri(environ: Mapping[str, str]) -> str:
    uri = environ.get(_TEST_MONGODB_URI_VARIABLE)
    if uri is None or not uri:
        raise pytest.UsageError(
            "TEST_MONGODB_URI is required for MongoDB integration tests"
        )

    invalid_uri_message = (
        "TEST_MONGODB_URI must be a credential-free loopback mongodb:// URI "
        "without a database path and with directConnection=true"
    )
    if uri != uri.strip():
        raise pytest.UsageError(invalid_uri_message)

    try:
        parsed = urlsplit(uri)
        port = parsed.port
        query_items = parse_qsl(
            parsed.query,
            keep_blank_values=True,
            strict_parsing=True,
        )
    except ValueError:
        raise pytest.UsageError(invalid_uri_message) from None

    host = parsed.hostname
    if (
        parsed.scheme != "mongodb"
        or host is None
        or not _is_loopback_host(host)
        or parsed.username is not None
        or parsed.password is not None
        or "," in parsed.netloc
        or parsed.path not in {"", "/"}
        or parsed.fragment
        or query_items != [("directConnection", "true")]
        or (port is not None and port <= 0)
    ):
        raise pytest.UsageError(invalid_uri_message)

    return uri


async def drop_test_database(settings: MongoTestSettings) -> None:
    """Drop only one database whose generated T004 name passes the guard."""
    if _TEST_DATABASE_NAME_PATTERN.fullmatch(settings.database_name) is None:
        raise RuntimeError("Refusing to drop an unrecognized MongoDB test database")

    client: AsyncMongoClient[dict[str, object]] = AsyncMongoClient(
        settings.uri,
        appname="telegram-assist-bot-t004-tests",
        connectTimeoutMS=_MONGODB_TIMEOUT_MILLISECONDS,
        serverSelectionTimeoutMS=_MONGODB_TIMEOUT_MILLISECONDS,
        socketTimeoutMS=_MONGODB_TIMEOUT_MILLISECONDS,
        timeoutMS=_MONGODB_TIMEOUT_MILLISECONDS,
        tz_aware=True,
    )
    try:
        async with asyncio.timeout(_MONGODB_TIMEOUT_MILLISECONDS / 1_000):
            await client.drop_database(settings.database_name)
    finally:
        async with asyncio.timeout(_MONGODB_TIMEOUT_MILLISECONDS / 1_000):
            await client.close()


@pytest.fixture
def mongodb_test_settings() -> Iterator[MongoTestSettings]:
    """Yield a unique loopback-only MongoDB database and remove it afterward."""
    settings = MongoTestSettings(
        uri=_read_test_mongodb_uri(os.environ),
        database_name=f"tab_t004_{uuid4().hex}",
    )
    try:
        yield settings
    finally:
        try:
            asyncio.run(drop_test_database(settings))
        except (PyMongoError, TimeoutError):
            raise RuntimeError("MongoDB test database cleanup failed") from None
