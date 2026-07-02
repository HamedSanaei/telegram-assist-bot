"""Collector worker: reads new posts from source channels via Telethon.

Runs as its own process (``python -m src.workers.collector``) so a crash
never takes down the approval bot. On first run, Telethon prompts for a
phone number and login code to create the user session file.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from telethon import TelegramClient, events
from telethon.utils import get_peer_id

from src.application.collect_post import CollectedMessage, CollectPostUseCase
from src.composition import (
    create_ai_service,
    create_mongo,
    create_repositories,
    create_sqlite,
    sync_config_to_sqlite,
)
from src.domain.entities import MediaItem
from src.domain.enums import MediaKind
from src.domain.interfaces import ChannelRepository
from src.shared.config import (
    AppConfig,
    load_configuration,
    log_startup_summary,
    validate_collector_config,
)
from src.shared.errors import AppError
from src.shared.logging_setup import get_logger, setup_logging

logger = get_logger(__name__)


class Collector:
    """
    Listens for new messages on source channels and feeds them into
    :class:`CollectPostUseCase`.

    Example:
        collector = Collector(client, use_case, Path("data/media"))
        await collector.run(source_channels)
    """

    def __init__(
        self,
        client: TelegramClient,
        use_case: CollectPostUseCase,
        media_directory: Path,
        channels: ChannelRepository | None = None,
    ) -> None:
        """
        Args:
            client: A Telethon client (user session with read access to
                the source channels).
            use_case: The post collection use case.
            media_directory: Directory photos are downloaded into.
            channels: Optional source channel repository used to persist
                resolved channel display names.
        """
        self._client = client
        self._use_case = use_case
        self._media_dir = media_directory
        self._channels = channels
        self._seen_album_keys: set[tuple[int, int]] = set()
        self._source_chat_ids: set[int] = set()

    async def run(
        self,
        sources: list[str | int],
        startup_backfill_since: datetime | None = None,
        startup_backfill_max_messages: int = 5000,
        source_refresh_seconds: int = 60,
    ) -> None:
        """
        Start listening until the client disconnects.

        Args:
            sources: Source channel usernames or numeric chat ids.
            startup_backfill_since: Earliest Telegram message date to scan
                during startup. When omitted, only live messages are handled.
            startup_backfill_max_messages: Safety cap for scanned messages
                per source. Set to 0 to disable the startup scan.
            source_refresh_seconds: How often configuration is reloaded to
                discover newly added source channels while the collector is
                already running.

        Side effects:
            Downloads media files and writes posts/queue items.
        """
        self._media_dir.mkdir(parents=True, exist_ok=True)
        resolved = await self._resolve_sources(sources)
        if not resolved:
            raise AppError("No source channel could be resolved")

        self._client.add_event_handler(self._on_new_message, events.NewMessage())
        self._client.add_event_handler(self._on_album, events.Album())
        logger.info("Collector listening on %d source channels", len(resolved))
        backfill_task = asyncio.create_task(
            self._run_backfill_safely(
                resolved,
                startup_backfill_since,
                startup_backfill_max_messages,
            )
        )
        refresh_task = asyncio.create_task(
            self._refresh_sources(
                source_refresh_seconds,
                startup_backfill_since,
                startup_backfill_max_messages,
            )
        )
        try:
            await self._client.run_until_disconnected()
        finally:
            for task in (backfill_task, refresh_task):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

    async def _resolve_sources(self, sources: list[str | int]) -> list[object]:
        """Resolve configured source identifiers and update the live filter."""
        resolved = []
        for source in sources:
            try:
                entity = await self._client.get_entity(source)
                chat_id = get_peer_id(entity)
                title = self._entity_title(entity)
                username = str(getattr(entity, "username", "") or "")
                resolved.append(entity)
                self._source_chat_ids.add(chat_id)
                if self._channels is not None:
                    await self._channels.upsert_source_details(
                        identifier=str(source),
                        chat_id=chat_id,
                        title=title,
                        username=username,
                    )
                logger.info(
                    "Resolved source channel source=%r chat_id=%s title=%s",
                    source,
                    chat_id,
                    title or username,
                )
            except Exception as exc:
                logger.error("Cannot resolve source channel %r: %s", source, exc)
        return resolved

    @staticmethod
    def _entity_title(entity: object) -> str:
        """Return a readable Telegram entity title if one is available."""
        return str(
            getattr(entity, "title", None)
            or getattr(entity, "first_name", None)
            or ""
        )

    async def _refresh_sources(
        self,
        source_refresh_seconds: int,
        startup_backfill_since: datetime | None,
        startup_backfill_max_messages: int,
    ) -> None:
        """Reload source channel configuration periodically while running."""
        if source_refresh_seconds <= 0:
            return
        while True:
            await asyncio.sleep(source_refresh_seconds)
            try:
                config = load_configuration()
                before = set(self._source_chat_ids)
                resolved = await self._resolve_sources(config.telegram.source_channels)
                resolved_ids = {get_peer_id(entity) for entity in resolved}
                self._source_chat_ids = resolved_ids
                new_entities = [
                    entity
                    for entity in resolved
                    if get_peer_id(entity) not in before
                ]
                if new_entities:
                    logger.info(
                        "Collector discovered %d new source channels",
                        len(new_entities),
                    )
                    refresh_backfill_since = (
                        _current_day_start_utc(config.scheduler.timezone)
                        if startup_backfill_since is not None
                        else None
                    )
                    await self._backfill_recent_messages(
                        new_entities,
                        refresh_backfill_since,
                        startup_backfill_max_messages,
                    )
            except Exception:
                logger.exception("Collector source refresh failed")

    async def _run_backfill_safely(
        self,
        entities: list[object],
        startup_backfill_since: datetime | None,
        startup_backfill_max_messages: int,
    ) -> None:
        """Run startup backfill without stopping live collection on failure."""
        try:
            await self._backfill_recent_messages(
                entities,
                startup_backfill_since,
                startup_backfill_max_messages,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Collector startup backfill failed")

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        """Handle one incoming message; errors are logged, never raised."""
        if event.chat_id not in self._source_chat_ids:
            return
        grouped_id = getattr(event.message, "grouped_id", None)
        if grouped_id is not None:
            logger.info(
                "Skipping album member in live handler chat=%s msg=%s grouped_id=%s",
                event.chat_id,
                event.message.id,
                grouped_id,
            )
            return
        await self._process_messages(event.chat_id, [event.message], origin="live")

    async def _on_album(self, event: events.Album.Event) -> None:
        """Handle a Telegram album as one collected post."""
        if event.chat_id not in self._source_chat_ids:
            return
        messages = list(event.messages)
        if not messages:
            return
        grouped_id = getattr(messages[0], "grouped_id", None)
        if grouped_id is not None:
            key = (event.chat_id, int(grouped_id))
            if key in self._seen_album_keys:
                logger.info(
                    "Skipping already processed album chat=%s grouped_id=%s",
                    event.chat_id,
                    grouped_id,
                )
                return
            self._seen_album_keys.add(key)
        await self._process_messages(event.chat_id, messages, origin="live_album")

    async def _backfill_recent_messages(
        self,
        entities: list[object],
        since: datetime | None,
        max_messages_per_source: int,
    ) -> None:
        """
        Process today's messages from each source at startup.

        Telethon's ``Got difference`` log lines mean the client synced state,
        but they do not guarantee that this process saw those messages as
        live events. This startup scan starts from the first message after
        the configured day boundary; exact duplicates are skipped by
        ``CollectPostUseCase``.
        """
        if since is None or max_messages_per_source <= 0:
            logger.info("Collector startup backfill disabled")
            return
        since = self._as_utc(since)
        for entity in entities:
            chat_id = get_peer_id(entity)
            messages: list[object] = []
            scanned = 0
            async for message in self._client.iter_messages(
                entity, limit=max_messages_per_source
            ):
                scanned += 1
                message_date = self._message_date(message)
                if message_date is not None and message_date < since:
                    break
                messages.append(message)
            logger.info(
                "Collector daily backfill source_chat=%s since=%s scanned=%d messages=%d",
                chat_id,
                since.isoformat(),
                scanned,
                len(messages),
            )
            for group in self._group_backfill_messages(reversed(messages)):
                grouped_id = getattr(group[0], "grouped_id", None)
                if grouped_id is not None:
                    self._seen_album_keys.add((chat_id, int(grouped_id)))
                origin = "backfill_album" if len(group) > 1 else "backfill"
                await self._process_messages(chat_id, group, origin=origin)

    @staticmethod
    def _as_utc(value: datetime) -> datetime:
        """Return an aware UTC datetime for reliable Telegram date comparison."""
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)

    @classmethod
    def _message_date(cls, message: object) -> datetime | None:
        """Return a Telethon message date as aware UTC, if available."""
        value = getattr(message, "date", None)
        if not isinstance(value, datetime):
            return None
        return cls._as_utc(value)

    @staticmethod
    def _group_backfill_messages(messages: Iterable[Any]) -> list[list[Any]]:
        """Group adjacent album members from a newest-first history scan."""
        groups: list[list[Any]] = []
        album_groups: dict[int, list[Any]] = {}
        for message in messages:
            grouped_id = getattr(message, "grouped_id", None)
            if grouped_id is None:
                groups.append([message])
                continue
            key = int(grouped_id)
            group = album_groups.setdefault(key, [])
            if not group:
                groups.append(group)
            group.append(message)
        return groups

    @staticmethod
    def _message_text(message: object) -> str:
        """Return the text/caption from a Telethon message."""
        return str(
            getattr(message, "raw_text", None)
            or getattr(message, "message", None)
            or ""
        )

    async def _process_messages(
        self, chat_id: int, messages: list[object], origin: str
    ) -> None:
        """Normalize one message or album and feed it into the use case."""
        first = messages[0]
        text = next(
            (
                self._message_text(message)
                for message in messages
                if self._message_text(message).strip()
            ),
            "",
        )
        photo_count = sum(1 for message in messages if self._media_kind(message) == MediaKind.PHOTO)
        video_count = sum(
            1 for message in messages if self._media_kind(message) == MediaKind.VIDEO
        )
        logger.info(
            "Received %s message chat=%s msg=%s count=%d text_len=%d photos=%d videos=%d",
            origin,
            chat_id,
            first.id,
            len(messages),
            len(text),
            photo_count,
            video_count,
        )
        try:
            media: list[MediaItem] = []
            for message in messages:
                media_kind = self._media_kind(message)
                if media_kind is not None:
                    path = await message.download_media(file=str(self._media_dir))
                    if path:
                        media.append(
                            MediaItem(
                                kind=media_kind,
                                file_path=str(path),
                                mime_type=self._media_mime_type(message),
                                file_size=self._media_size(message),
                            )
                        )
            collected = CollectedMessage(
                source_chat_id=chat_id,
                message_id=first.id,
                text=text,
                media=media,
            )
            await self._use_case.handle_new_message(collected)
        except AppError as exc:
            cause = exc.__cause__
            logger.error(
                "Collection failed chat=%s msg=%s error=%s%s",
                chat_id,
                first.id,
                exc,
                f" (caused by: {cause})" if cause is not None else "",
            )
        except Exception:
            logger.exception(
                "Unexpected collection error chat=%s msg=%s", chat_id, first.id
            )

    @staticmethod
    def _media_kind(message: object) -> MediaKind | None:
        """Detect the supported media kind for a Telethon message."""
        if getattr(message, "photo", None) is not None:
            return MediaKind.PHOTO
        if getattr(message, "video", None) is not None:
            return MediaKind.VIDEO
        if getattr(message, "document", None) is not None:
            mime_type = Collector._media_mime_type(message) or ""
            if mime_type.startswith("video/"):
                return MediaKind.VIDEO
            return MediaKind.DOCUMENT
        return None

    @staticmethod
    def _media_mime_type(message: object) -> str | None:
        """Return the Telegram media MIME type when available."""
        file_info = getattr(message, "file", None)
        mime_type = getattr(file_info, "mime_type", None)
        if mime_type:
            return str(mime_type)
        document = getattr(message, "document", None)
        mime_type = getattr(document, "mime_type", None)
        return str(mime_type) if mime_type else None

    @staticmethod
    def _media_size(message: object) -> int | None:
        """Return the Telegram media file size when available."""
        file_info = getattr(message, "file", None)
        size = getattr(file_info, "size", None)
        if isinstance(size, int):
            return size
        document = getattr(message, "document", None)
        size = getattr(document, "size", None)
        return size if isinstance(size, int) else None


async def run(config: AppConfig | None = None) -> None:
    """
    Build dependencies and run the collector until disconnect.

    Args:
        config: Optional pre-loaded configuration (mainly for tests).

    Raises:
        ConfigurationError: When collector configuration is incomplete.
    """
    config = config or load_configuration()
    setup_logging(config.logging.level, config.logging.file)
    log_startup_summary(config)
    validate_collector_config(config)

    db = await create_sqlite(config)
    await sync_config_to_sqlite(config, db)
    repos = create_repositories(db)
    mongo_client, posts = create_mongo(config)
    await posts.ensure_indexes()
    ai = create_ai_service(config)

    use_case = CollectPostUseCase(
        posts=posts,
        queue=repos["queue"],
        ai=ai,
        retention_days=config.storage.retention_days,
        recent_compare_limit=config.ai.recent_posts_compare_limit,
        vpn_testing_enabled=config.vpn_testing.iran_worker_enabled,
    )
    client = TelegramClient(
        config.telegram.collector_session,
        int(config.telegram.api_id),
        config.telegram.api_hash,
    )
    collector = Collector(
        client,
        use_case,
        Path(config.storage.media_directory),
        channels=repos["channels"],
    )
    startup_backfill_since = _current_day_start_utc(config.scheduler.timezone)
    try:
        await client.start()
        await collector.run(
            config.telegram.source_channels,
            startup_backfill_since=startup_backfill_since,
            startup_backfill_max_messages=(
                config.telegram.collector_daily_backfill_max_messages
            ),
            source_refresh_seconds=config.telegram.source_refresh_seconds,
        )
    finally:
        await client.disconnect()
        mongo_client.close()
        await db.close()


def _current_day_start_utc(timezone_name: str) -> datetime:
    """
    Return the start of the current Gregorian day in the configured timezone.

    Args:
        timezone_name: IANA timezone name such as ``Asia/Tehran``.

    Returns:
        The local midnight converted to UTC for Telethon message-date
        comparisons.

    Raises:
        ZoneInfoNotFoundError: When ``timezone_name`` is not installed or
            invalid.
    """
    local_timezone = ZoneInfo(timezone_name)
    local_midnight = datetime.now(local_timezone).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    return local_midnight.astimezone(timezone.utc)


def main() -> None:
    """Synchronous entrypoint for ``python -m src.workers.collector``."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
