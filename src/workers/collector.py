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
from src.application.runtime_lease_service import RuntimeLeaseService
from src.application.source_metrics_service import SourceMetricsService
from src.composition import (
    create_ai_service,
    create_mongo,
    create_repositories,
    create_runtime_lease_store,
    create_sqlite,
    sync_config_to_sqlite,
)
from src.domain.entities import MediaItem, PostSourceMetrics, QueueItem, TextEntity
from src.domain.enums import MediaKind, QueueItemType, QueueStatus
from src.domain.interfaces import ChannelRepository
from src.domain.services.vpn_parser import extract_vpn_configs
from src.shared.config import (
    AppConfig,
    load_configuration,
    log_startup_summary,
    validate_collector_config,
)
from src.shared.errors import (
    AppError,
    ApplicationAlreadyRunningError,
    RuntimeLeaseLostError,
)
from src.shared.logging_setup import get_logger, setup_logging
from src.infrastructure.telegram.telethon_publish import TelethonSourceMetadataRefresher
from src.workers.config_sync import ConfigSyncWorker
from src.workers.queue_worker import QueueWorker

logger = get_logger(__name__)

_RUNTIME_CATCH_UP_MAX_MESSAGES = 300
_DISCOVERY_REFRESH_SECONDS = 300
_DISCOVERY_CATCH_UP_MAX_MESSAGES = 100


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
        media_download_timeout_seconds: int = 60,
    ) -> None:
        """
        Args:
            client: A Telethon client (user session with read access to
                the source channels).
            use_case: The post collection use case.
            media_directory: Directory photos are downloaded into.
            channels: Optional source channel repository used to persist
                resolved channel display names.
            media_download_timeout_seconds: Maximum seconds to wait for a
                single media download before continuing with the text-only
                post.
        """
        self._client = client
        self._use_case = use_case
        self._media_dir = media_directory
        self._channels = channels
        self._media_download_timeout_seconds = media_download_timeout_seconds
        self._seen_album_keys: set[tuple[int, int]] = set()
        self._source_chat_ids: set[int] = set()
        self._discovery_chat_ids: set[int] = set()
        self._chat_labels: dict[int, str] = {}
        self._message_locks: dict[tuple[int, int], asyncio.Lock] = {}
        self._timezone_name = "Asia/Tehran"

    async def run(
        self,
        sources: list[str | int],
        startup_backfill_since: datetime | None = None,
        startup_backfill_max_messages: int = 5000,
        source_refresh_seconds: int = 60,
        timezone_name: str = "Asia/Tehran",
    ) -> None:
        """
        Start listening until the client disconnects.

        Args:
            sources: Source channel usernames or numeric chat ids.
            startup_backfill_since: Earliest Telegram message date to scan
                during startup. When omitted, only live messages are handled.
            startup_backfill_max_messages: Safety cap for scanned messages
                per source. Set to 0 to disable the startup scan.
            source_refresh_seconds: How often the source channel list is
                reloaded (from SQLite, where the management bot writes) to
                discover newly added source channels while running.
            timezone_name: Timezone whose day boundary limits the backfill
                of newly discovered sources.

        Side effects:
            Downloads media files and writes posts/queue items.
        """
        self._timezone_name = timezone_name
        self._media_dir.mkdir(parents=True, exist_ok=True)
        resolved = await self._resolve_sources(sources)
        if not resolved:
            logger.warning(
                "No configured source channel resolved; continuing with VPN dialog discovery"
            )

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
        discovery_task = asyncio.create_task(
            self._run_discovery_backfill_safely(
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
        discovery_refresh_task = asyncio.create_task(
            self._refresh_discovery_dialogs(startup_backfill_since)
        )
        try:
            await self._client.run_until_disconnected()
        finally:
            for task in (
                backfill_task,
                discovery_task,
                refresh_task,
                discovery_refresh_task,
            ):
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
                self._chat_labels[chat_id] = title or (
                    f"@{username}" if username else str(chat_id)
                )
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

    async def _current_source_identifiers(self) -> list[str | int]:
        """
        Return the up-to-date source channel list.

        SQLite is the live list read by the collector. It is updated by
        management-bot commands and by the runtime config sync worker, where
        ``configuration.json`` is authoritative for source-channel lists.
        """
        if self._channels is not None:
            return list(await self._channels.list_sources())
        return list(load_configuration().telegram.source_channels)

    async def _refresh_sources(
        self,
        source_refresh_seconds: int,
        startup_backfill_since: datetime | None,
        startup_backfill_max_messages: int,
    ) -> None:
        """Reload the source channel list periodically while running."""
        if source_refresh_seconds <= 0:
            return
        while True:
            await asyncio.sleep(source_refresh_seconds)
            try:
                identifiers = await self._current_source_identifiers()
                before = set(self._source_chat_ids)
                resolved = await self._resolve_sources(identifiers)
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
                        _current_day_start_utc(self._timezone_name)
                        if startup_backfill_since is not None
                        else None
                    )
                    await self._backfill_recent_messages(
                        new_entities,
                        refresh_backfill_since,
                        startup_backfill_max_messages,
                    )
                known_entities = [
                    entity
                    for entity in resolved
                    if get_peer_id(entity) in before
                ]
                await self._runtime_catch_up(
                    known_entities,
                    startup_backfill_since,
                    startup_backfill_max_messages,
                )
            except Exception:
                logger.exception("Collector source refresh failed")

    async def _runtime_catch_up(
        self,
        entities: list[object],
        startup_backfill_since: datetime | None,
        startup_backfill_max_messages: int,
    ) -> None:
        """
        Periodically rescan recent current-day source history while running.

        Telethon may reconnect and log ``Got difference`` without delivering
        every missed source-channel update to this process as a live event.
        This lightweight catch-up pass keeps collection idempotent by relying
        on MongoDB source-message identity checks before media download or AI
        analysis.
        """
        if (
            not entities
            or startup_backfill_since is None
            or startup_backfill_max_messages <= 0
        ):
            return
        max_messages = min(
            startup_backfill_max_messages,
            _RUNTIME_CATCH_UP_MAX_MESSAGES,
        )
        since = _current_day_start_utc(self._timezone_name)
        logger.info(
            "Collector runtime catch-up source_count=%d since=%s max_messages=%d",
            len(entities),
            since.isoformat(),
            max_messages,
        )
        await self._backfill_recent_messages(entities, since, max_messages)

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

    async def _run_discovery_backfill_safely(
        self,
        since: datetime | None,
        max_messages_per_dialog: int,
    ) -> None:
        """Resolve joined groups/channels and scan today's config posts."""
        try:
            entities = await self._resolve_discovery_dialogs()
            logger.info(
                "Collector VPN discovery listening on %d non-configured dialogs",
                len(entities),
            )
            await self._backfill_recent_messages(
                entities,
                since,
                max_messages_per_dialog,
                discovery=True,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Collector VPN discovery startup backfill failed")

    async def _resolve_discovery_dialogs(self) -> list[object]:
        """Return joined channels/groups excluding configured sources/destinations."""
        destination_ids: set[int] = set()
        if self._channels is not None:
            destination_ids = {
                channel.chat_id for channel in await self._channels.list_destinations()
            }
        excluded = set(self._source_chat_ids).union(destination_ids)
        entities: dict[int, object] = {}
        for archived in (False, True):
            try:
                iterator = self._client.iter_dialogs(archived=archived)
                async for dialog in iterator:
                    if not (
                        bool(getattr(dialog, "is_channel", False))
                        or bool(getattr(dialog, "is_group", False))
                    ):
                        continue
                    entity = getattr(dialog, "entity", None)
                    if entity is None:
                        continue
                    chat_id = get_peer_id(entity)
                    if chat_id not in excluded:
                        entities[chat_id] = entity
                        self._chat_labels[chat_id] = self._entity_title(entity) or str(chat_id)
            except TypeError:
                if archived:
                    continue
                async for dialog in self._client.iter_dialogs():
                    if not (
                        bool(getattr(dialog, "is_channel", False))
                        or bool(getattr(dialog, "is_group", False))
                    ):
                        continue
                    entity = getattr(dialog, "entity", None)
                    if entity is None:
                        continue
                    chat_id = get_peer_id(entity)
                    if chat_id not in excluded:
                        entities[chat_id] = entity
                        self._chat_labels[chat_id] = self._entity_title(entity) or str(chat_id)
        self._discovery_chat_ids = set(entities)
        return list(entities.values())

    async def _refresh_discovery_dialogs(self, since: datetime | None) -> None:
        """Refresh dialog membership and perform bounded config catch-up scans."""
        while True:
            await asyncio.sleep(_DISCOVERY_REFRESH_SECONDS)
            try:
                before = set(self._discovery_chat_ids)
                entities = await self._resolve_discovery_dialogs()
                new_entities = [
                    entity for entity in entities if get_peer_id(entity) not in before
                ]
                if new_entities:
                    await self._backfill_recent_messages(
                        new_entities,
                        _current_day_start_utc(self._timezone_name),
                        _RUNTIME_CATCH_UP_MAX_MESSAGES,
                        discovery=True,
                    )
                if since is not None:
                    await self._backfill_recent_messages(
                        entities,
                        _current_day_start_utc(self._timezone_name),
                        _DISCOVERY_CATCH_UP_MAX_MESSAGES,
                        discovery=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Collector VPN discovery refresh failed")

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        """Handle one incoming message; errors are logged, never raised."""
        is_source = event.chat_id in self._source_chat_ids
        is_discovery = event.chat_id in self._discovery_chat_ids
        if not is_source and not is_discovery:
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
        await self._process_messages(
            event.chat_id,
            [event.message],
            origin="live" if is_source else "vpn_discovery_live",
            discovery=is_discovery and not is_source,
        )

    async def _on_album(self, event: events.Album.Event) -> None:
        """Handle a Telegram album as one collected post."""
        is_source = event.chat_id in self._source_chat_ids
        is_discovery = event.chat_id in self._discovery_chat_ids
        if not is_source and not is_discovery:
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
        await self._process_messages(
            event.chat_id,
            messages,
            origin="live_album" if is_source else "vpn_discovery_live_album",
            discovery=is_discovery and not is_source,
        )

    async def _backfill_recent_messages(
        self,
        entities: list[object],
        since: datetime | None,
        max_messages_per_source: int,
        discovery: bool = False,
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
        per_source_groups: list[tuple[int, list[list[object]]]] = []
        total_groups = 0
        for entity in entities:
            chat_id = get_peer_id(entity)
            messages: list[object] = []
            scanned = 0
            try:
                async for message in self._client.iter_messages(
                    entity, limit=max_messages_per_source
                ):
                    scanned += 1
                    message_date = self._message_date(message)
                    if message_date is not None and message_date < since:
                        break
                    if discovery and not extract_vpn_configs(
                        self._message_text(message)
                    ):
                        continue
                    messages.append(message)
            except Exception:
                logger.exception(
                    "Collector daily backfill failed for source_chat=%s after scanned=%d",
                    chat_id,
                    scanned,
                )
                per_source_groups.append((chat_id, []))
                continue
            logger.info(
                "Collector daily backfill source_chat=%s since=%s scanned=%d messages=%d",
                chat_id,
                since.isoformat(),
                scanned,
                len(messages),
            )
            groups = self._group_backfill_messages(reversed(messages))
            total_groups += len(groups)
            per_source_groups.append((chat_id, groups))

        logger.info(
            "Collector daily backfill queued source_count=%d groups=%d strategy=round_robin",
            len(per_source_groups),
            total_groups,
        )
        while any(groups for _, groups in per_source_groups):
            for chat_id, groups in per_source_groups:
                if not groups:
                    continue
                group = groups.pop(0)
                grouped_id = getattr(group[0], "grouped_id", None)
                if grouped_id is not None:
                    self._seen_album_keys.add((chat_id, int(grouped_id)))
                origin = "backfill_album" if len(group) > 1 else "backfill"
                await self._process_messages(
                    chat_id,
                    group,
                    origin=(f"vpn_discovery_{origin}" if discovery else origin),
                    discovery=discovery,
                )

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

    @staticmethod
    def _message_text_entities(message: object) -> list[TextEntity]:
        """
        Return custom emoji entities from a Telethon message.

        Telethon exposes premium emoji as ``MessageEntityCustomEmoji`` with a
        ``document_id``. The domain stores a framework-neutral copy so
        destination publishing can recreate Telethon formatting entities.
        """
        result: list[TextEntity] = []
        for entity in getattr(message, "entities", None) or []:
            document_id = getattr(entity, "document_id", None)
            if document_id is None:
                continue
            offset = getattr(entity, "offset", None)
            length = getattr(entity, "length", None)
            if not isinstance(offset, int) or not isinstance(length, int):
                continue
            result.append(
                TextEntity(
                    kind="custom_emoji",
                    offset=offset,
                    length=length,
                    data={"document_id": int(document_id)},
                )
            )
        return result

    async def _process_messages(
        self,
        chat_id: int,
        messages: list[object],
        origin: str,
        discovery: bool = False,
    ) -> None:
        """Serialize every ingestion path for one source message or album."""
        first = messages[0]
        grouped_id = getattr(first, "grouped_id", None)
        identity = int(grouped_id) if grouped_id is not None else int(first.id)
        lock = self._message_locks.setdefault((chat_id, identity), asyncio.Lock())
        async with lock:
            await self._process_messages_locked(
                chat_id,
                messages,
                origin,
                discovery,
            )

    async def _process_messages_locked(
        self,
        chat_id: int,
        messages: list[object],
        origin: str,
        discovery: bool = False,
    ) -> None:
        """Normalize one message or album and feed it into the use case."""
        first = messages[0]
        text_message = next(
            (message for message in messages if self._message_text(message).strip()),
            first,
        )
        text = self._message_text(text_message)
        if discovery and not extract_vpn_configs(text):
            return
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
            grouped_id = getattr(first, "grouped_id", None)
            grouped_id_int = int(grouped_id) if grouped_id is not None else None
            expected_media_count = sum(
                1 for message in messages if self._media_kind(message) is not None
            )
            should_download_media = await self._should_download_media(
                chat_id,
                first.id,
                grouped_id_int,
                expected_media_count,
            )
            media: list[MediaItem] = []
            if not should_download_media:
                logger.info(
                    "Skipping media download for stored source chat=%s msg=%s grouped_id=%s",
                    chat_id,
                    first.id,
                    grouped_id_int,
                )
            else:
                for message in messages:
                    media_kind = self._media_kind(message)
                    if media_kind is not None:
                        item = await self._download_media_item(message, media_kind)
                        if item is not None:
                            media.append(item)
            collected = CollectedMessage(
                source_chat_id=chat_id,
                message_id=first.id,
                source_label=self._chat_labels.get(chat_id, str(chat_id)),
                grouped_id=grouped_id_int,
                text=text,
                text_entities=self._message_text_entities(text_message),
                media=media,
                expected_media_count=expected_media_count,
                source_metrics=self._source_metrics(first),
            )
            if discovery:
                await self._use_case.handle_vpn_discovery_message(collected)
            else:
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

    async def _should_download_media(
        self,
        chat_id: int,
        message_id: int,
        grouped_id: int | None,
        expected_media_count: int,
    ) -> bool:
        """
        Return whether media should be downloaded for the source message.

        Newer use cases can request media repair for stored posts that were
        previously saved without attachments. Older fakes only implement
        ``has_seen_source_message`` and keep the historical skip behavior.
        """
        decision = getattr(self._use_case, "should_download_media", None)
        if decision is not None:
            return bool(
                await decision(chat_id, message_id, grouped_id, expected_media_count)
            )
        return not await self._use_case.has_seen_source_message(
            chat_id,
            message_id,
            grouped_id,
        )

    async def _download_media_item(
        self, message: object, media_kind: MediaKind
    ) -> MediaItem | None:
        """
        Download one media item with a timeout.

        Args:
            message: Telethon message object.
            media_kind: Detected media kind.

        Returns:
            A stored media item, or ``None`` when download failed.

        Side effects:
            Writes the media file to ``self._media_dir`` when successful.
            Logs and skips the attachment when Telegram stalls or fails so
            the post text can still be stored and sent to approval.
        """
        message_id = getattr(message, "id", "?")
        file_size = self._media_size(message) or 0
        estimated_seconds = int(file_size / (128 * 1024)) + 30
        timeout_seconds = min(
            600,
            max(1, self._media_download_timeout_seconds, estimated_seconds),
        )
        path: str | None = None
        for attempt in range(1, 3):
            try:
                path = await asyncio.wait_for(
                    message.download_media(file=str(self._media_dir)),
                    timeout=timeout_seconds,
                )
                break
            except asyncio.TimeoutError:
                logger.warning(
                    "Media download timed out msg=%s kind=%s timeout=%ss attempt=%d",
                    message_id,
                    media_kind.value,
                    timeout_seconds,
                    attempt,
                )
            except Exception as exc:
                logger.warning(
                    "Media download failed msg=%s kind=%s attempt=%d error=%s",
                    message_id,
                    media_kind.value,
                    attempt,
                    exc,
                )
            if attempt == 1:
                await asyncio.sleep(2)
        if path is None:
            logger.error(
                "Media download exhausted retries msg=%s kind=%s; post continues text-only",
                message_id,
                media_kind.value,
            )
            return None
        if not path:
            logger.warning(
                "Media download returned empty path msg=%s kind=%s; continuing without media",
                message_id,
                media_kind.value,
            )
            return None
        return MediaItem(
            kind=media_kind,
            file_path=str(path),
            mime_type=self._media_mime_type(message),
            file_size=self._media_size(message),
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

    @staticmethod
    def _source_metrics(message: object) -> PostSourceMetrics:
        """Extract source engagement metrics from a Telethon message."""
        replies = getattr(message, "replies", None)
        reactions = getattr(message, "reactions", None)
        return PostSourceMetrics(
            views=Collector._int_or_none(getattr(message, "views", None)),
            forwards=Collector._int_or_none(getattr(message, "forwards", None)),
            replies_count=Collector._int_or_none(getattr(replies, "replies", None)),
            reactions_count=Collector._reaction_count(reactions),
            source_published_at=Collector._message_date(message),
        )

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        """Return an integer value, or ``None`` when unavailable."""
        return value if isinstance(value, int) else None

    @staticmethod
    def _reaction_count(reactions: object) -> int | None:
        """Return the summed Telegram reaction count when available."""
        results = getattr(reactions, "results", None)
        if not results:
            return None
        total = 0
        found = False
        for item in results:
            count = getattr(item, "count", None)
            if isinstance(count, int):
                total += count
                found = True
        return total if found else None


async def run(
    config: AppConfig | None = None,
    configure_logging: bool = True,
    runtime_lease: RuntimeLeaseService | None = None,
) -> None:
    """
    Build dependencies and run the collector until disconnect.

    Args:
        config: Optional pre-loaded configuration (mainly for tests).
        configure_logging: Whether this entrypoint should configure root
            logging. ``src.run_all`` sets this to ``False`` so one process-wide
            run log captures every component.
        runtime_lease: Lease already acquired and heartbeated by
            :mod:`src.run_all`. When omitted, this entrypoint owns its own
            collector lease.

    Raises:
        ConfigurationError: When collector configuration is incomplete.
    """
    config = config or load_configuration()
    if configure_logging:
        setup_logging(
            config.logging.level,
            config.logging.file,
            color_console=config.logging.color_console,
            entrypoint_name="collector",
        )
    log_startup_summary(config)
    validate_collector_config(config)

    if runtime_lease is not None:
        if not runtime_lease.is_acquired:
            raise RuntimeError("Externally managed collector lease is not acquired")
        await _run_collector_application(config)
        return

    lease_client, lease_repository = create_runtime_lease_store(config)
    owned_lease = RuntimeLeaseService(
        lease_repository,
        "collector",
        (
            str(config.telegram.api_id),
            config.telegram.collector_session,
        ),
    )
    try:
        await owned_lease.acquire()
        await owned_lease.run_with_heartbeat(_run_collector_application(config))
    finally:
        await owned_lease.release()
        lease_client.close()


async def _run_collector_application(config: AppConfig) -> None:
    """
    Build and run collector dependencies after lease acquisition.

    Args:
        config: Validated application configuration.
    """

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
        media_download_timeout_seconds=config.storage.media_download_timeout_seconds,
    )
    source_metrics = SourceMetricsService(
        posts,
        repos["queue"],
        TelethonSourceMetadataRefresher(client),
    )

    async def handle_source_metrics(item: QueueItem) -> QueueStatus:
        """Refresh source engagement with the collector Telegram session."""
        await source_metrics.refresh_post(str(item.payload["post_id"]))
        return QueueStatus.COMPLETED

    metrics_worker = QueueWorker(
        repos["queue"],
        {QueueItemType.SOURCE_METRICS_REFRESH: handle_source_metrics},
    )
    config_sync = ConfigSyncWorker(db)
    config_sync_task = asyncio.create_task(config_sync.run())
    startup_backfill_since = _current_day_start_utc(config.scheduler.timezone)
    initial_sources = _ordered_unique_sources(
        list(config.telegram.source_channels),
        list(await repos["channels"].list_sources()),
    )
    metrics_task: asyncio.Task[None] | None = None
    try:
        await client.start()
        metrics_task = asyncio.create_task(metrics_worker.run())
        await collector.run(
            initial_sources,
            startup_backfill_since=startup_backfill_since,
            startup_backfill_max_messages=(
                config.telegram.collector_daily_backfill_max_messages
            ),
            source_refresh_seconds=config.telegram.source_refresh_seconds,
            timezone_name=config.scheduler.timezone,
        )
    finally:
        metrics_worker.stop()
        if metrics_task is not None:
            metrics_task.cancel()
            try:
                await metrics_task
            except asyncio.CancelledError:
                pass
        config_sync.stop()
        config_sync_task.cancel()
        try:
            await config_sync_task
        except asyncio.CancelledError:
            pass
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


def _ordered_unique_sources(
    config_sources: list[str | int], sqlite_sources: list[str | int]
) -> list[str | int]:
    """
    Merge configured and SQLite-managed source identifiers without duplicates.

    Args:
        config_sources: Sources listed in ``configuration.json``.
        sqlite_sources: Enabled runtime sources stored in SQLite.

    Returns:
        Config sources first, followed by SQLite-only sources.

    Notes:
        The collector uses this at startup so a source explicitly present in
        the config still participates in same-day backfill even if an older
        SQLite row was disabled during development.
    """
    merged: list[str | int] = []
    seen: set[str] = set()
    for source in [*config_sources, *sqlite_sources]:
        key = str(source).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        merged.append(source)
    return merged


def main() -> None:
    """Synchronous entrypoint for ``python -m src.workers.collector``."""
    try:
        asyncio.run(run())
    except ApplicationAlreadyRunningError as exc:
        logger.error("Collector startup refused: %s", exc)
    except RuntimeLeaseLostError as exc:
        logger.error("Collector stopped after lease loss: %s", exc)


if __name__ == "__main__":
    main()
