"""Verify private media containment and ordered Telethon album mapping."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from telegram_assist_bot.application.ports import (
    PublicationMedia,
    PublicationPayload,
    PublisherError,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.infrastructure.telegram.user_publisher import (
    TelethonPublisherGateway,
)

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Message:
    id: int


class Client:
    def __init__(self) -> None:
        self.files: object = None
        self.kwargs: dict[str, object] = {}

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        raise AssertionError("Album must use send_file.")

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        assert entity == -1008
        self.files, self.kwargs = file, kwargs
        return [Message(10), Message(11)]


def test_sends_album_in_stored_order_and_returns_all_ids(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    first, second = root / "first.jpg", root / "second.mp4"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    expires = datetime.now(UTC) + timedelta(days=1)
    payload = PublicationPayload(
        -1008,
        "کپشن\u200cآلبوم🙂",
        (),
        (
            PublicationMedia(MediaType.PHOTO, "first.jpg", expires),
            PublicationMedia(MediaType.VIDEO, "second.mp4", expires),
        ),
    )
    client = Client()
    result = asyncio.run(
        TelethonPublisherGateway(client, media_root=root).publish(
            payload, timeout_seconds=2
        )
    )
    assert result.message_ids == (10, 11)
    assert client.files == [str(first.resolve()), str(second.resolve())]
    assert client.kwargs["caption"] == "کپشن\u200cآلبوم🙂"


def test_rejects_missing_and_outside_media(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    outside = tmp_path / "private.bin"
    outside.write_bytes(b"secret")
    expires = datetime.now(UTC) + timedelta(days=1)
    client = Client()
    gateway = TelethonPublisherGateway(client, media_root=root)
    for value in ("missing.bin", str(outside)):
        payload = PublicationPayload(
            -1008,
            None,
            (),
            (PublicationMedia(MediaType.DOCUMENT, value, expires),),
        )
        with pytest.raises(PublisherError):
            asyncio.run(gateway.publish(payload, timeout_seconds=2))
