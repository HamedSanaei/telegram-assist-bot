"""Exercise Telethon publication mapping with sanitized SDK fixtures."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

import pytest
from telethon import types  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import PublicationPayload, PublisherError
from telegram_assist_bot.domain import PublicationFailureCategory
from telegram_assist_bot.domain.posts import TelegramEntity
from telegram_assist_bot.infrastructure.telegram.user_publisher import (
    TelethonPublisherGateway,
)
from telegram_assist_bot.shared.config import LogLevel
from telegram_assist_bot.shared.observability import Redactor, StructuredLogger

if TYPE_CHECKING:
    from pathlib import Path

    from telegram_assist_bot.shared.observability import EventSink


@dataclass
class Message:
    """Represent one sanitized SDK response."""

    id: int


class Client:
    """Capture the exact User API call."""

    def __init__(self) -> None:
        self.call: tuple[int, str, dict[str, object]] | None = None

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        self.call = (entity, message, kwargs)
        return Message(44)

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        raise AssertionError("Text publication must not use media sending.")

    async def upload_file(self, file: object, **kwargs: object) -> object:
        raise AssertionError("Text publication must not upload media.")


def test_maps_persian_zwnj_and_custom_emoji_without_bot_metadata(
    tmp_path: Path,
) -> None:
    client = Client()
    gateway = TelethonPublisherGateway(client, media_root=tmp_path)
    entity = TelegramEntity(9, 2, "custom_emoji", "987654")
    result = asyncio.run(
        gateway.publish(
            PublicationPayload(
                -1009,
                "خبر\u200cویژه\n🎉",  # noqa: RUF001
                (entity,),
            ),
            timeout_seconds=2,
        )
    )
    assert result.message_ids == (44,)
    assert client.call is not None
    destination, text, kwargs = client.call
    assert destination == -1009
    assert text == "خبر\u200cویژه\n🎉"  # noqa: RUF001
    mapped = kwargs["formatting_entities"]
    assert isinstance(mapped, list)
    assert isinstance(mapped[0], types.MessageEntityCustomEmoji)
    assert mapped[0].offset == 9
    assert mapped[0].document_id == 987654
    assert "header" not in kwargs
    assert "administrator" not in kwargs


def test_maps_common_entity_and_rejects_unknown_entity(tmp_path: Path) -> None:
    client = Client()
    gateway = TelethonPublisherGateway(client, media_root=tmp_path)
    asyncio.run(
        gateway.publish(
            PublicationPayload(-1, "bold", (TelegramEntity(0, 4, "bold"),)),
            timeout_seconds=1,
        )
    )
    assert client.call is not None
    entities = client.call[2]["formatting_entities"]
    assert isinstance(entities, list)
    assert isinstance(entities[0], types.MessageEntityBold)
    with pytest.raises(PublisherError) as captured:
        asyncio.run(
            gateway.publish(
                PublicationPayload(-1, "x", (TelegramEntity(0, 1, "unknown"),)),
                timeout_seconds=1,
            )
        )
    assert captured.value.category is PublicationFailureCategory.PERMANENT
    assert not captured.value.request_may_have_reached_telegram
    assert captured.value.reason_code == "invalid_publication_payload"


def test_maps_text_url_with_persian_utf16_offsets(tmp_path: Path) -> None:
    client = Client()
    gateway = TelethonPublisherGateway(client, media_root=tmp_path)

    asyncio.run(
        gateway.publish(
            PublicationPayload(
                -1009,
                "سلام 👋 لینک",
                (
                    TelegramEntity(
                        8,
                        4,
                        "text_url",
                        url="https://example.invalid/path",
                    ),
                ),
            ),
            timeout_seconds=1,
        )
    )

    assert client.call is not None
    mapped = client.call[2]["formatting_entities"]
    assert isinstance(mapped, list)
    assert isinstance(mapped[0], types.MessageEntityTextUrl)
    assert mapped[0].offset == 8
    assert mapped[0].length == 4
    assert mapped[0].url == "https://example.invalid/path"


def test_legacy_text_url_is_omitted_and_logged_without_blocking_send(
    tmp_path: Path,
) -> None:
    client = Client()
    events: list[dict[str, object]] = []
    logger = StructuredLogger(
        sink=cast("EventSink", events.append),
        clock=lambda: datetime(2026, 7, 16, tzinfo=UTC),
        redactor=Redactor(),
        minimum_level=LogLevel.DEBUG,
    )
    gateway = TelethonPublisherGateway(client, media_root=tmp_path, logger=logger)

    result = asyncio.run(
        gateway.publish(
            PublicationPayload(
                -1009,
                "سلام 👋 لینک",
                (TelegramEntity(8, 4, "text_url"),),
            ),
            timeout_seconds=1,
        )
    )

    assert result.message_ids == (44,)
    assert client.call is not None
    assert client.call[2]["formatting_entities"] == []
    assert events == [
        {
            "timestamp": events[0]["timestamp"],
            "level": "WARNING",
            "event_name": "publication_entity_omitted",
            "correlation_id": None,
            "target_destination_id": -1009,
            "entity_kind": "text_url",
            "omission_reason": "missing_url_metadata",
            "omitted_count": 1,
        }
    ]


def test_invalid_entity_bounds_are_typed_pre_send_and_never_call_telegram(
    tmp_path: Path,
) -> None:
    client = Client()
    gateway = TelethonPublisherGateway(client, media_root=tmp_path)

    with pytest.raises(PublisherError) as captured:
        asyncio.run(
            gateway.publish(
                PublicationPayload(-1009, "کوتاه", (TelegramEntity(99, 1, "bold"),)),
                timeout_seconds=1,
            )
        )

    assert captured.value.category is PublicationFailureCategory.PERMANENT
    assert not captured.value.request_may_have_reached_telegram
    assert captured.value.reason_code == "invalid_publication_payload"
    assert client.call is None


def test_invalid_response_is_a_safe_ambiguous_failure(tmp_path: Path) -> None:
    class BrokenClient(Client):
        async def send_message(
            self, entity: int, message: str, **kwargs: object
        ) -> object:
            del entity, message, kwargs
            return object()

    gateway = TelethonPublisherGateway(BrokenClient(), media_root=tmp_path)
    with pytest.raises(PublisherError) as captured:
        asyncio.run(
            gateway.publish(PublicationPayload(-1, "text", ()), timeout_seconds=1)
        )
    assert captured.value.category is PublicationFailureCategory.AMBIGUOUS


def test_timeout_is_unknown_and_cancellation_propagates(tmp_path: Path) -> None:
    class SlowClient(Client):
        async def send_message(
            self, entity: int, message: str, **kwargs: object
        ) -> object:
            del entity, message, kwargs
            await asyncio.sleep(1)
            return Message(1)

    gateway = TelethonPublisherGateway(SlowClient(), media_root=tmp_path)
    with pytest.raises(PublisherError) as captured:
        asyncio.run(
            gateway.publish(PublicationPayload(-1, "text", ()), timeout_seconds=0.001)
        )
    assert captured.value.category is PublicationFailureCategory.TIMEOUT
    assert captured.value.request_may_have_reached_telegram

    class CancelledClient(Client):
        async def send_message(
            self, entity: int, message: str, **kwargs: object
        ) -> object:
            del entity, message, kwargs
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            TelethonPublisherGateway(CancelledClient(), media_root=tmp_path).publish(
                PublicationPayload(-1, "text", ()), timeout_seconds=1
            )
        )
