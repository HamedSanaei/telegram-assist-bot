"""Unit tests for Telethon destination publishing."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.domain.entities import Post, TextEntity
from src.infrastructure.telegram.telethon_publish import TelethonDestinationPublisher


class FakeMessage:
    """Minimal Telethon-like message result."""

    def __init__(self, message_id: int) -> None:
        """Args: message_id: Telegram message id to expose."""
        self.id = message_id


class FakeTelethonClient:
    """Fake Telethon client that records send calls."""

    def __init__(self) -> None:
        """Initialize empty call logs."""
        self.sent_messages: list[dict[str, Any]] = []
        self.sent_files: list[dict[str, Any]] = []
        self.deleted: list[tuple[int, list[int]]] = []

    async def get_entity(self, chat_id: int) -> int:
        """Return the chat id as the resolved fake entity."""
        return chat_id

    async def send_message(self, entity: int, text: str, **kwargs: Any) -> FakeMessage:
        """Record one text send."""
        self.sent_messages.append({"entity": entity, "text": text, "kwargs": kwargs})
        return FakeMessage(101)

    async def send_file(self, entity: int, path: str, **kwargs: Any) -> FakeMessage:
        """Record one file send."""
        self.sent_files.append({"entity": entity, "path": path, "kwargs": kwargs})
        return FakeMessage(102)

    async def delete_messages(self, entity: int, ids: list[int]) -> None:
        """Record one delete request."""
        self.deleted.append((entity, ids))


def _post() -> Post:
    """Build a post containing one custom emoji entity."""
    text = "premium *"
    return Post(
        post_id="p1",
        source_chat_id=-100,
        source_message_id=1,
        text=text,
        content_hash="hash",
        text_entities=[
            TextEntity(
                kind="custom_emoji",
                offset=text.index("*"),
                length=1,
                data={"document_id": 123456789},
            )
        ],
    )


class TestTelethonDestinationPublisher:
    """Tests for :class:`TelethonDestinationPublisher`."""

    async def test_publish_post_passes_custom_emoji_entities(self) -> None:
        client = FakeTelethonClient()
        publisher = TelethonDestinationPublisher(client)  # type: ignore[arg-type]

        message_id = await publisher.publish_post(-100200, _post())

        assert message_id == 101
        kwargs = client.sent_messages[0]["kwargs"]
        entities = kwargs["formatting_entities"]
        assert entities[0].document_id == 123456789
        assert entities[0].offset == _post().text.index("*")

    async def test_schedule_post_passes_custom_emoji_entities(self) -> None:
        client = FakeTelethonClient()
        publisher = TelethonDestinationPublisher(client)  # type: ignore[arg-type]
        scheduled_at = datetime.now(timezone.utc)

        message_id = await publisher.schedule_post(-100200, _post(), scheduled_at)

        assert message_id == 101
        kwargs = client.sent_messages[0]["kwargs"]
        entities = kwargs["formatting_entities"]
        assert kwargs["schedule"] == scheduled_at
        assert entities[0].document_id == 123456789

    async def test_delete_message_uses_destination_entity(self) -> None:
        client = FakeTelethonClient()
        publisher = TelethonDestinationPublisher(client)  # type: ignore[arg-type]

        await publisher.delete_message(-100200, 55)

        assert client.deleted == [(-100200, [55])]
