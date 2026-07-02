"""Unit tests for the Telethon collector orchestration."""

from __future__ import annotations

from pathlib import Path

from src.workers import collector as collector_module
from src.workers.collector import Collector


class FakeMessage:
    """Minimal Telethon-like message used by collector tests."""

    def __init__(self, message_id: int, text: str) -> None:
        self.id = message_id
        self.message = text
        self.photo = None


class FakeEntity:
    """Minimal Telethon-like channel entity."""

    title = "Source"


class FakeClient:
    """Small fake for the Telethon client methods used by Collector."""

    def __init__(self) -> None:
        self.entity = FakeEntity()
        self.handlers: list[object] = []
        self.messages = [
            FakeMessage(2, "newest"),
            FakeMessage(1, "oldest"),
        ]

    async def get_entity(self, source: str) -> FakeEntity:
        """Resolve any source to the fake entity."""
        return self.entity

    def add_event_handler(self, handler: object, event: object) -> None:
        """Record event handler registration."""
        self.handlers.append(handler)

    async def iter_messages(self, entity: FakeEntity, limit: int):
        """Yield recent messages in Telethon's newest-first order."""
        for message in self.messages[:limit]:
            yield message

    async def run_until_disconnected(self) -> None:
        """Return immediately so tests do not block."""
        return None


class FakeUseCase:
    """Records collected messages passed by the collector."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []

    async def handle_new_message(self, message) -> None:
        """Record the normalized collected message."""
        self.calls.append(
            (message.source_chat_id, message.message_id, message.text)
        )


class TestCollectorBackfill:
    """Tests for startup backfill behavior."""

    async def test_startup_backfill_processes_recent_messages_oldest_first(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(["@source"], startup_backfill_limit=2)

        assert client.handlers
        assert use_case.calls == [
            (-100123, 1, "oldest"),
            (-100123, 2, "newest"),
        ]

    async def test_startup_backfill_can_be_disabled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(["@source"], startup_backfill_limit=0)

        assert client.handlers
        assert use_case.calls == []
