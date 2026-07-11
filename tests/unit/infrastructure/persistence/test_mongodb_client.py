from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

import pytest
from pymongo.errors import PyMongoError

from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MINIMUM_MONGODB_WIRE_VERSION,
    POSTS_COLLECTION_NAME,
    MongoConnectionError,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
    verify_mongodb_connection,
)
from telegram_assist_bot.shared.config import (
    MongoConfig,
    ResolvedSecrets,
    SecretReference,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

    from pymongo import AsyncMongoClient

    from telegram_assist_bot.infrastructure.persistence.mongodb.client import (
        MongoDocument,
    )

_URI_ENVIRONMENT_VARIABLE = "T004_MONGODB_URI"
_DRIVER_DETAIL = "private" + "-driver-detail"


def _run[T](coroutine: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coroutine)


def _config(*, timeout_seconds: int = 5) -> MongoConfig:
    return MongoConfig(
        uri=SecretReference(environment_variable=_URI_ENVIRONMENT_VARIABLE),
        database_name="tab_t004_unit",
        connect_timeout_seconds=timeout_seconds,
    )


def _assert_redacted(error: Exception, *sensitive_values: str) -> None:
    for sensitive_value in sensitive_values:
        assert sensitive_value not in str(error)
        assert sensitive_value not in repr(error)
    assert error.__cause__ is None
    assert error.__context__ is None


@dataclass(slots=True)
class _FakeAdmin:
    max_wire_version: int = MINIMUM_MONGODB_WIRE_VERSION
    failure: BaseException | None = None
    commands: list[dict[str, object]] = field(default_factory=list)

    async def command(self, command: dict[str, object]) -> dict[str, object]:
        self.commands.append(command)
        if self.failure is not None:
            raise self.failure
        if command == {"hello": 1}:
            return {"maxWireVersion": self.max_wire_version}
        return {"ok": 1}


@dataclass(slots=True)
class _FakeClient:
    admin: _FakeAdmin
    close_failure: BaseException | None = None
    closed: bool = False

    async def close(self) -> None:
        self.closed = True
        if self.close_failure is not None:
            raise self.close_failure


def _typed_client(fake: _FakeClient) -> AsyncMongoClient[MongoDocument]:
    return cast("AsyncMongoClient[MongoDocument]", fake)


def test_client_uses_bounded_options_and_disables_hidden_driver_retries() -> None:
    config = _config()
    client = create_mongodb_client(
        config,
        ResolvedSecrets(
            {
                _URI_ENVIRONMENT_VARIABLE: (
                    "mongodb://127.0.0.1:27017/?directConnection=true"
                )
            }
        ),
    )
    try:
        collection = get_posts_collection(client, config)

        assert client.options.timeout == config.connect_timeout_seconds
        assert client.options.retry_reads is False
        assert client.options.retry_writes is False
        assert collection.name == POSTS_COLLECTION_NAME
        assert collection.database.name == config.database_name
    finally:
        _run(close_mongodb_client(client, timeout_seconds=1))


def test_invalid_uri_is_mapped_without_retaining_credentials() -> None:
    username = "synthetic" + "-user"
    credential = "synthetic" + "-credential"
    invalid_uri = f"mongodb://{username}:{credential}@127.0.0.1:not-a-port/"

    with pytest.raises(MongoConnectionError) as captured:
        create_mongodb_client(
            _config(),
            ResolvedSecrets({_URI_ENVIRONMENT_VARIABLE: invalid_uri}),
        )

    _assert_redacted(captured.value, username, credential, invalid_uri)


def test_connection_verification_checks_ping_and_minimum_wire_version() -> None:
    admin = _FakeAdmin()

    _run(
        verify_mongodb_connection(
            _typed_client(_FakeClient(admin)),
            timeout_seconds=1,
        )
    )

    assert admin.commands == [{"ping": 1}, {"hello": 1}]


def test_unsupported_server_and_driver_failure_are_safe_connection_errors() -> None:
    clients = (
        _FakeClient(_FakeAdmin(max_wire_version=MINIMUM_MONGODB_WIRE_VERSION - 1)),
        _FakeClient(_FakeAdmin(failure=PyMongoError(_DRIVER_DETAIL))),
    )

    for client in clients:
        with pytest.raises(MongoConnectionError) as captured:
            _run(verify_mongodb_connection(_typed_client(client), timeout_seconds=1))
        _assert_redacted(captured.value, _DRIVER_DETAIL)


def test_close_failure_is_bounded_and_redacted() -> None:
    fake = _FakeClient(
        _FakeAdmin(),
        close_failure=PyMongoError(_DRIVER_DETAIL),
    )

    with pytest.raises(MongoConnectionError) as captured:
        _run(close_mongodb_client(_typed_client(fake), timeout_seconds=1))

    assert fake.closed is True
    _assert_redacted(captured.value, _DRIVER_DETAIL)
