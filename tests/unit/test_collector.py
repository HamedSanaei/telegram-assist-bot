"""Unit tests for the Telethon collector orchestration."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

from src.domain.enums import MediaKind
from src.workers import collector as collector_module
from src.workers.collector import Collector


TODAY_START = datetime(2026, 7, 2, tzinfo=timezone.utc)


class FakeFileInfo:
    """Minimal Telethon-like file metadata."""

    mime_type = "video/mp4"
    size = 1234


class FakeMessage:
    """Minimal Telethon-like message used by collector tests."""

    def __init__(
        self,
        message_id: int,
        text: str,
        date: datetime,
        has_video: bool = False,
    ) -> None:
        self.id = message_id
        self.message = text
        self.date = date
        self.photo = None
        self.video = object() if has_video else None
        self.document = self.video
        self.file = FakeFileInfo() if has_video else None

    async def download_media(self, file: str) -> str:
        """Pretend to download media into the requested directory."""
        path = Path(file) / f"{self.id}.mp4"
        path.write_bytes(b"video")
        return str(path)


class FakeEntity:
    """Minimal Telethon-like channel entity."""

    title = "Source"


class FakeClient:
    """Small fake for the Telethon client methods used by Collector."""

    def __init__(self) -> None:
        self.entity = FakeEntity()
        self.handlers: list[object] = []
        self.messages = [
            FakeMessage(
                3,
                "newest today",
                datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
            ),
            FakeMessage(
                2,
                "oldest today",
                datetime(2026, 7, 2, 1, tzinfo=timezone.utc),
            ),
            FakeMessage(
                1,
                "yesterday",
                datetime(2026, 7, 1, 23, 59, tzinfo=timezone.utc),
            ),
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
        await asyncio.sleep(0.01)
        return None


class FakeUseCase:
    """Records collected messages passed by the collector."""

    def __init__(self) -> None:
        self.calls: list[tuple[int, int, str]] = []
        self.media_kinds: list[list[MediaKind]] = []

    async def handle_new_message(self, message) -> None:
        """Record the normalized collected message."""
        self.calls.append(
            (message.source_chat_id, message.message_id, message.text)
        )
        self.media_kinds.append([media.kind for media in message.media])


class TestCollectorBackfill:
    """Tests for startup backfill behavior."""

    async def test_startup_backfill_processes_current_day_messages_oldest_first(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@source"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=10,
            source_refresh_seconds=0,
        )

        assert client.handlers
        assert use_case.calls == [
            (-100123, 2, "oldest today"),
            (-100123, 3, "newest today"),
        ]

    async def test_startup_backfill_can_be_disabled(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@source"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=0,
            source_refresh_seconds=0,
        )

        assert client.handlers
        assert use_case.calls == []

    async def test_video_backfill_is_collected_as_video_media(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        client.messages = [
            FakeMessage(
                4,
                "video today",
                datetime(2026, 7, 2, 10, tzinfo=timezone.utc),
                has_video=True,
            )
        ]
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@source"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=10,
            source_refresh_seconds=0,
        )

        assert use_case.calls == [(-100123, 4, "video today")]
        assert use_case.media_kinds == [[MediaKind.VIDEO]]
