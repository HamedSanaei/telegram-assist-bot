"""Verify private media containment and ordered Telethon album mapping."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, cast

import pytest
from telethon import types  # type: ignore[import-untyped]

from telegram_assist_bot.application.ports import (
    PublicationMedia,
    PublicationPayload,
    PublisherError,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import TelegramEntity
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
        self.uploads: list[tuple[object, dict[str, object]]] = []

    async def send_message(self, entity: int, message: str, **kwargs: object) -> object:
        raise AssertionError("Album must use send_file.")

    async def send_file(self, entity: int, file: object, **kwargs: object) -> object:
        assert entity == -1008
        self.files, self.kwargs = file, kwargs
        count = len(file) if isinstance(file, list) else 1
        return [Message(10 + index) for index in range(count)]

    async def upload_file(self, file: object, **kwargs: object) -> object:
        self.uploads.append((file, kwargs))
        return type("Uploaded", (), {"name": kwargs["file_name"]})()


def test_sends_album_in_stored_order_and_returns_all_ids(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    names = ("a" * 64, "b" * 64, "c" * 64, "d" * 64)
    paths = tuple(root / name for name in names)
    for index, path in enumerate(paths):
        path.write_bytes(f"item-{index}".encode())
    expires = datetime.now(UTC) + timedelta(days=1)
    payload = PublicationPayload(
        -1008,
        "کپشن\u200cآلبوم🙂",
        (TelegramEntity(0, 5, "bold"),),
        (
            PublicationMedia(
                MediaType.PHOTO,
                names[0],
                expires,
                mime_type="image/jpeg",
                original_filename="source.jpg",
            ),
            PublicationMedia(MediaType.VIDEO, names[1], expires),
            PublicationMedia(
                MediaType.ANIMATION,
                names[2],
                expires,
                mime_type="video/mp4",
                original_filename="motion.mp4",
            ),
            PublicationMedia(
                MediaType.DOCUMENT,
                names[3],
                expires,
                mime_type="application/pdf",
                original_filename="report.pdf",
            ),
        ),
    )
    client = Client()
    result = asyncio.run(
        TelethonPublisherGateway(client, media_root=root).publish(
            payload, timeout_seconds=2
        )
    )
    assert result.message_ids == (10, 11, 12, 13)
    values = client.files
    assert isinstance(values, list)
    assert isinstance(values[0], types.InputMediaUploadedPhoto)
    assert isinstance(values[1], types.InputMediaUploadedDocument)
    assert isinstance(values[2], types.InputMediaUploadedDocument)
    assert isinstance(values[3], types.InputMediaUploadedDocument)
    video = values[1]
    animation = values[2]
    document = values[3]
    assert any(
        isinstance(value, types.DocumentAttributeVideo) and value.supports_streaming
        for value in video.attributes
    )
    assert any(
        isinstance(value, types.DocumentAttributeAnimated)
        for value in animation.attributes
    )
    assert document.force_file
    assert client.uploads == [
        (str(paths[0].resolve()), {"file_name": "source.jpg"}),
        (str(paths[1].resolve()), {"file_name": "publication-video.mp4"}),
        (str(paths[2].resolve()), {"file_name": "motion.mp4"}),
        (str(paths[3].resolve()), {"file_name": "report.pdf"}),
    ]
    assert client.kwargs["caption"] == "کپشن\u200cآلبوم🙂"
    assert cast("list[object]", client.kwargs["formatting_entities"])


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
