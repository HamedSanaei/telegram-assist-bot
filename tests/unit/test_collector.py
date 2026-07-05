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
        download_delay_seconds: float = 0,
        entities: list[object] | None = None,
    ) -> None:
        self.id = message_id
        self.message = text
        self.entities = entities or []
        self.date = date
        self.photo = None
        self.video = object() if has_video else None
        self.document = self.video
        self.file = FakeFileInfo() if has_video else None
        self.download_count = 0
        self.download_delay_seconds = download_delay_seconds

    async def download_media(self, file: str) -> str:
        """Pretend to download media into the requested directory."""
        self.download_count += 1
        if self.download_delay_seconds > 0:
            await asyncio.sleep(self.download_delay_seconds)
        path = Path(file) / f"{self.id}.mp4"
        path.write_bytes(b"video")
        return str(path)


class FakeEntity:
    """Minimal Telethon-like channel entity."""

    def __init__(self, name: str = "source") -> None:
        """Args: name: Source identifier used by the fake client."""
        self.name = name
        self.title = f"Source {name}"


class FakeCustomEmojiEntity:
    """Minimal custom emoji text entity exposed by Telethon."""

    def __init__(self, offset: int, length: int, document_id: int) -> None:
        """Args: offset: Entity offset; length: Entity length; document_id: Emoji id."""
        self.offset = offset
        self.length = length
        self.document_id = document_id


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
        return FakeEntity(source)

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
        self.text_entities: list[list[object]] = []
        self.seen: set[tuple[int, int, int | None]] = set()
        self.media_counts: dict[tuple[int, int, int | None], int] = {}

    async def has_seen_source_message(
        self, source_chat_id: int, message_id: int, grouped_id: int | None = None
    ) -> bool:
        """Return whether the fake already knows the source identity."""
        return (source_chat_id, message_id, grouped_id) in self.seen

    async def should_download_media(
        self,
        source_chat_id: int,
        message_id: int,
        grouped_id: int | None,
        expected_media_count: int,
    ) -> bool:
        """Return whether stored media is incomplete."""
        key = (source_chat_id, message_id, grouped_id)
        if key not in self.seen:
            return expected_media_count > 0
        return self.media_counts.get(key, 0) < expected_media_count

    async def handle_new_message(self, message) -> None:
        """Record the normalized collected message."""
        self.calls.append(
            (message.source_chat_id, message.message_id, message.text)
        )
        self.media_kinds.append([media.kind for media in message.media])
        self.text_entities.append(list(message.text_entities))


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

    async def test_stored_source_message_does_not_download_media_again(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        video = FakeMessage(
            4,
            "video today",
            datetime(2026, 7, 2, 10, tzinfo=timezone.utc),
            has_video=True,
        )
        client.messages = [video]
        use_case = FakeUseCase()
        key = (-100123, 4, None)
        use_case.seen.add(key)
        use_case.media_counts[key] = 1
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@source"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=10,
            source_refresh_seconds=0,
        )

        assert video.download_count == 0
        assert use_case.calls == [(-100123, 4, "video today")]
        assert use_case.media_kinds == [[]]

    async def test_stored_source_message_with_missing_media_downloads_again(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Stored text-only posts can repair missing video media on backfill."""
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        video = FakeMessage(
            7,
            "stored video missing media",
            datetime(2026, 7, 2, 10, tzinfo=timezone.utc),
            has_video=True,
        )
        client.messages = [video]
        use_case = FakeUseCase()
        use_case.seen.add((-100123, 7, None))
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@source"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=10,
            source_refresh_seconds=0,
        )

        assert video.download_count == 1
        assert use_case.calls == [(-100123, 7, "stored video missing media")]
        assert use_case.media_kinds == [[MediaKind.VIDEO]]

    async def test_media_download_timeout_does_not_block_post_ingestion(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        use_case = FakeUseCase()
        collector = Collector(
            client,
            use_case,
            tmp_path,
            media_download_timeout_seconds=1,
        )
        message = FakeMessage(
            5,
            "video with slow download",
            datetime(2026, 7, 2, 10, tzinfo=timezone.utc),
            has_video=True,
            download_delay_seconds=2,
        )

        await collector._process_messages(-100123, [message], origin="backfill")

        assert message.download_count == 1
        assert use_case.calls == [(-100123, 5, "video with slow download")]
        assert use_case.media_kinds == [[]]

    async def test_custom_emoji_entities_are_collected(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Collector stores premium custom emoji entities with source text."""
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        text = "premium *"
        client.messages = [
            FakeMessage(
                6,
                text,
                datetime(2026, 7, 2, 10, tzinfo=timezone.utc),
                entities=[FakeCustomEmojiEntity(text.index("*"), 1, 987654321)],
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

        assert use_case.calls == [(-100123, 6, text)]
        assert use_case.text_entities[0][0].data["document_id"] == 987654321

    async def test_startup_backfill_interleaves_sources_round_robin(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def fake_peer_id(entity: FakeEntity) -> int:
            return {"@first": -1001, "@second": -1002}[entity.name]

        monkeypatch.setattr(collector_module, "get_peer_id", fake_peer_id)
        client = FakeClient()
        client.messages = [
            FakeMessage(12, "first newest", datetime(2026, 7, 2, 12, tzinfo=timezone.utc)),
            FakeMessage(11, "first oldest", datetime(2026, 7, 2, 11, tzinfo=timezone.utc)),
        ]

        async def iter_messages(entity: FakeEntity, limit: int):
            messages = {
                "@first": client.messages,
                "@second": [
                    FakeMessage(
                        22,
                        "second newest",
                        datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
                    ),
                    FakeMessage(
                        21,
                        "second oldest",
                        datetime(2026, 7, 2, 11, tzinfo=timezone.utc),
                    ),
                ],
            }[entity.name]
            for message in messages[:limit]:
                yield message

        client.iter_messages = iter_messages
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@first", "@second"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=10,
            source_refresh_seconds=0,
        )

        assert use_case.calls == [
            (-1001, 11, "first oldest"),
            (-1002, 21, "second oldest"),
            (-1001, 12, "first newest"),
            (-1002, 22, "second newest"),
        ]

    async def test_backfill_source_failure_does_not_stop_other_sources(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        def fake_peer_id(entity: FakeEntity) -> int:
            return {"@first": -1001, "@broken": -1002, "@third": -1003}[entity.name]

        monkeypatch.setattr(collector_module, "get_peer_id", fake_peer_id)
        client = FakeClient()

        async def iter_messages(entity: FakeEntity, limit: int):
            if entity.name == "@broken":
                raise RuntimeError("history unavailable")
            message_id = 10 if entity.name == "@first" else 30
            yield FakeMessage(
                message_id,
                f"{entity.name} today",
                datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
            )

        client.iter_messages = iter_messages
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)

        await collector.run(
            ["@first", "@broken", "@third"],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=10,
            source_refresh_seconds=0,
        )

        assert use_case.calls == [
            (-1001, 10, "@first today"),
            (-1003, 30, "@third today"),
        ]

    async def test_runtime_catch_up_rescans_existing_sources_with_small_limit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.setattr(collector_module, "get_peer_id", lambda entity: -100123)
        client = FakeClient()
        use_case = FakeUseCase()
        collector = Collector(client, use_case, tmp_path)
        calls: list[tuple[list[object], datetime | None, int]] = []

        async def fake_backfill(
            entities: list[object],
            since: datetime | None,
            max_messages_per_source: int,
        ) -> None:
            calls.append((entities, since, max_messages_per_source))

        monkeypatch.setattr(collector, "_backfill_recent_messages", fake_backfill)

        await collector._runtime_catch_up(
            [FakeEntity("@source")],
            startup_backfill_since=TODAY_START,
            startup_backfill_max_messages=5000,
        )

        assert len(calls) == 1
        assert calls[0][2] == 300
        assert calls[0][1] is not None
        assert calls[0][1].tzinfo is timezone.utc

    def test_startup_sources_include_config_even_when_sqlite_differs(self) -> None:
        sources = collector_module._ordered_unique_sources(
            ["@wbnet", "@alonews"], ["@wbnet", "@sqlite_only"]
        )

        assert sources == ["@wbnet", "@alonews", "@sqlite_only"]
