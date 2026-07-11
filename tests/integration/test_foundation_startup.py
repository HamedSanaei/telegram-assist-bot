"""Exercise the worker-free foundation lifecycle against test-only MongoDB."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest
from pymongo import ASCENDING

import telegram_assist_bot.bootstrap.runtime as bootstrap_runtime
from telegram_assist_bot.bootstrap import (
    FoundationConfigurationError,
    FoundationInfrastructureError,
    create_foundation_application,
)
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    POST_EXPIRATION_INDEX_NAME,
    POST_SOURCE_IDENTITY_INDEX_NAME,
    close_mongodb_client,
    create_mongodb_client,
    get_posts_collection,
)
from telegram_assist_bot.shared.config import load_configuration

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from telegram_assist_bot.shared.observability import EventSink, StructuredEvent
    from tests.integration.infrastructure.persistence.conftest import (
        MongoTestSettings,
    )

pytestmark = pytest.mark.integration

_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_EXAMPLE_CONFIGURATION_PATH = _REPOSITORY_ROOT / "config" / "configuration.example.json"
_VALID_LIFECYCLE_EVENTS = (
    "startup_begun",
    "configuration_validation_succeeded",
    "logging_initialized",
    "mongodb_connected",
    "indexes_ready",
    "application_ready",
    "shutdown_begun",
    "resource_closed",
    "shutdown_completed",
)


def _read_example_configuration() -> dict[str, object]:
    raw = _EXAMPLE_CONFIGURATION_PATH.read_text(encoding="utf-8")
    document: object = json.loads(raw)
    assert isinstance(document, dict)
    return cast("dict[str, object]", document)


def _write_configuration(
    tmp_path: Path,
    *,
    database_name: str,
    timeout_seconds: int = 5,
    valid: bool = True,
) -> Path:
    document = deepcopy(_read_example_configuration())
    mongodb = cast("dict[str, object]", document["mongodb"])
    mongodb["database_name"] = database_name
    mongodb["connect_timeout_seconds"] = timeout_seconds if valid else 0
    path = tmp_path / "foundation.configuration.json"
    path.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def _synthetic_environment(mongodb_uri: str) -> dict[str, str]:
    def synthetic(label: str) -> str:
        return f"fixture-{label}-value"

    return {
        "TAB_MONGODB_URI": mongodb_uri,
        "TAB_TELEGRAM_API_ID": "123456",
        "TAB_TELEGRAM_API_HASH": synthetic("telegram-api-hash"),
        "TAB_TELEGRAM_PHONE_NUMBER": synthetic("telegram-phone-number"),
        "TAB_TELEGRAM_BOT_TOKEN": synthetic("telegram-bot-token"),
        "TAB_AI_PROVIDER_KEY": synthetic("ai-provider-key"),
    }


def _event_names(
    events: list[StructuredEvent],
    *,
    correlation_id: str,
) -> tuple[str, ...]:
    return tuple(
        cast("str", event["event_name"])
        for event in events
        if event["correlation_id"] == correlation_id
    )


def _assert_one_correlation_id(
    events: list[StructuredEvent],
    *,
    expected: str,
) -> None:
    assert events
    assert {event["correlation_id"] for event in events} == {expected}


def _constant_correlation_id(value: str) -> Callable[[], str]:
    def factory() -> str:
        return value

    return factory


def test_real_startup_is_repeatable_and_initializes_exact_indexes(
    tmp_path: Path,
    mongodb_test_settings: MongoTestSettings,
) -> None:
    configuration_path = _write_configuration(
        tmp_path,
        database_name=mongodb_test_settings.database_name,
    )
    environ = _synthetic_environment(mongodb_test_settings.uri)
    events: list[StructuredEvent] = []
    correlation_ids = ("corr-foundation-real-1", "corr-foundation-real-2")

    async def scenario() -> None:
        for correlation_id in correlation_ids:
            application = create_foundation_application(
                sink=cast("EventSink", events.append),
                correlation_id_factory=_constant_correlation_id(correlation_id),
            )
            await application.start(configuration_path, environ=environ)
            assert application.is_ready is True
            assert application.correlation_id == correlation_id
            assert application.repository is not None
            await application.shutdown()
            assert application.is_ready is False

        loaded = load_configuration(configuration_path, environ=environ)
        client = create_mongodb_client(loaded.settings.mongodb, loaded.secrets)
        try:
            collection = get_posts_collection(client, loaded.settings.mongodb)
            async with asyncio.timeout(loaded.settings.mongodb.connect_timeout_seconds):
                cursor = await collection.list_indexes()
                documents = await cursor.to_list()
        finally:
            await close_mongodb_client(
                client,
                timeout_seconds=loaded.settings.mongodb.connect_timeout_seconds,
            )

        indexes = {
            cast("str", document["name"]): cast("Mapping[str, object]", document)
            for document in documents
        }
        assert set(indexes) == {
            "_id_",
            POST_SOURCE_IDENTITY_INDEX_NAME,
            POST_EXPIRATION_INDEX_NAME,
        }
        source_index = indexes[POST_SOURCE_IDENTITY_INDEX_NAME]
        expiration_index = indexes[POST_EXPIRATION_INDEX_NAME]
        assert tuple(cast("Mapping[str, int]", source_index["key"]).items()) == (
            ("source_channel_id", ASCENDING),
            ("source_message_id", ASCENDING),
        )
        assert source_index["unique"] is True
        assert tuple(cast("Mapping[str, int]", expiration_index["key"]).items()) == (
            ("expires_at", ASCENDING),
        )
        assert expiration_index["expireAfterSeconds"] == 0

    asyncio.run(scenario())

    for correlation_id in correlation_ids:
        assert _event_names(events, correlation_id=correlation_id) == (
            _VALID_LIFECYCLE_EVENTS
        )
    assert len(events) == len(_VALID_LIFECYCLE_EVENTS) * len(correlation_ids)


def test_invalid_configuration_never_constructs_a_mongodb_client(
    tmp_path: Path,
    mongodb_test_settings: MongoTestSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configuration_path = _write_configuration(
        tmp_path,
        database_name=mongodb_test_settings.database_name,
        valid=False,
    )
    environ = _synthetic_environment(mongodb_test_settings.uri)
    events: list[StructuredEvent] = []
    client_attempts = 0

    def unexpected_client_factory(*_args: object, **_kwargs: object) -> object:
        nonlocal client_attempts
        client_attempts += 1
        raise AssertionError("MongoDB client creation must follow validation.")

    monkeypatch.setattr(
        bootstrap_runtime,
        "create_mongodb_client",
        unexpected_client_factory,
    )
    application = create_foundation_application(
        sink=cast("EventSink", events.append),
        correlation_id_factory=_constant_correlation_id(
            "corr-foundation-invalid-config"
        ),
    )

    async def scenario() -> None:
        with pytest.raises(FoundationConfigurationError):
            await application.start(configuration_path, environ=environ)

    asyncio.run(scenario())

    assert client_attempts == 0
    assert application.is_ready is False
    _assert_one_correlation_id(
        events,
        expected="corr-foundation-invalid-config",
    )
    assert _event_names(
        events,
        correlation_id="corr-foundation-invalid-config",
    ) == (
        "startup_begun",
        "configuration_validation_failed",
        "startup_failed",
    )


def test_unavailable_credential_bearing_target_is_bounded_and_redacted(
    tmp_path: Path,
    mongodb_test_settings: MongoTestSettings,
) -> None:
    username = "synthetic" + "-startup-user"
    credential = "synthetic" + "-startup-credential"
    unavailable_uri = (
        f"mongodb://{username}:{credential}@127.0.0.1:1/?directConnection=true"
    )
    configuration_path = _write_configuration(
        tmp_path,
        database_name=mongodb_test_settings.database_name,
        timeout_seconds=1,
    )
    environ = _synthetic_environment(unavailable_uri)
    events: list[StructuredEvent] = []
    application = create_foundation_application(
        sink=cast("EventSink", events.append),
        correlation_id_factory=_constant_correlation_id("corr-foundation-unavailable"),
    )

    async def scenario() -> FoundationInfrastructureError:
        with pytest.raises(FoundationInfrastructureError) as captured:
            async with asyncio.timeout(3):
                await application.start(configuration_path, environ=environ)
        return captured.value

    error = asyncio.run(scenario())

    assert application.is_ready is False
    _assert_one_correlation_id(events, expected="corr-foundation-unavailable")
    assert _event_names(
        events,
        correlation_id="corr-foundation-unavailable",
    ) == (
        "startup_begun",
        "configuration_validation_succeeded",
        "logging_initialized",
        "startup_failed",
        "shutdown_begun",
        "resource_closed",
        "shutdown_completed",
    )
    rendered = json.dumps(events, ensure_ascii=False) + str(error) + repr(error)
    if error.__cause__ is not None:
        rendered += str(error.__cause__) + repr(error.__cause__)
    for sensitive_value in (
        username,
        credential,
        unavailable_uri,
        "127.0.0.1",
    ):
        assert sensitive_value not in rendered
