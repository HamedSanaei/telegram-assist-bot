"""Collector worker: reads new posts from source channels via Telethon.

Runs as its own process (``python -m src.workers.collector``) so a crash
never takes down the approval bot. On first run, Telethon prompts for a
phone number and login code to create the user session file.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

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
    ) -> None:
        """
        Args:
            client: A Telethon client (user session with read access to
                the source channels).
            use_case: The post collection use case.
            media_directory: Directory photos are downloaded into.
        """
        self._client = client
        self._use_case = use_case
        self._media_dir = media_directory

    async def run(
        self, sources: list[str | int], startup_backfill_limit: int = 10
    ) -> None:
        """
        Start listening until the client disconnects.

        Args:
            sources: Source channel usernames or numeric chat ids.
            startup_backfill_limit: Number of recent messages to scan per
                source when the collector starts. Set to 0 to disable the
                startup scan.

        Side effects:
            Downloads media files and writes posts/queue items.
        """
        self._media_dir.mkdir(parents=True, exist_ok=True)
        resolved = []
        for source in sources:
            try:
                entity = await self._client.get_entity(source)
                resolved.append(entity)
                logger.info(
                    "Resolved source channel source=%r chat_id=%s title=%s",
                    source,
                    get_peer_id(entity),
                    getattr(entity, "title", None) or getattr(entity, "username", ""),
                )
            except Exception as exc:
                logger.error("Cannot resolve source channel %r: %s", source, exc)
        if not resolved:
            raise AppError("No source channel could be resolved")

        self._client.add_event_handler(
            self._on_new_message, events.NewMessage(chats=resolved)
        )
        logger.info("Collector listening on %d source channels", len(resolved))
        await self._backfill_recent_messages(resolved, startup_backfill_limit)
        await self._client.run_until_disconnected()

    async def _on_new_message(self, event: events.NewMessage.Event) -> None:
        """Handle one incoming message; errors are logged, never raised."""
        await self._process_message(event.chat_id, event.message, origin="live")

    async def _backfill_recent_messages(
        self, entities: list[object], limit_per_source: int
    ) -> None:
        """
        Process recent messages from each source at startup.

        Telethon's ``Got difference`` log lines mean the client synced state,
        but they do not guarantee that this process saw those messages as
        live events. This startup scan makes restarts and first runs
        deterministic; exact duplicates are skipped by ``CollectPostUseCase``.
        """
        if limit_per_source <= 0:
            logger.info("Collector startup backfill disabled")
            return
        for entity in entities:
            chat_id = get_peer_id(entity)
            messages = [
                message
                async for message in self._client.iter_messages(
                    entity, limit=limit_per_source
                )
            ]
            logger.info(
                "Collector startup backfill source_chat=%s messages=%d",
                chat_id,
                len(messages),
            )
            for message in reversed(messages):
                await self._process_message(chat_id, message, origin="backfill")

    async def _process_message(self, chat_id: int, message: object, origin: str) -> None:
        """Normalize one Telethon message and feed it into the use case."""
        logger.info(
            "Received %s message chat=%s msg=%s text_len=%d has_photo=%s",
            origin,
            chat_id,
            message.id,
            len(message.message or ""),
            message.photo is not None,
        )
        try:
            media: list[MediaItem] = []
            if message.photo is not None:
                path = await message.download_media(file=str(self._media_dir))
                if path:
                    media.append(MediaItem(kind=MediaKind.PHOTO, file_path=str(path)))
            collected = CollectedMessage(
                source_chat_id=chat_id,
                message_id=message.id,
                text=message.message or "",
                media=media,
            )
            await self._use_case.handle_new_message(collected)
        except AppError as exc:
            cause = exc.__cause__
            logger.error(
                "Collection failed chat=%s msg=%s error=%s%s",
                chat_id,
                message.id,
                exc,
                f" (caused by: {cause})" if cause is not None else "",
            )
        except Exception:
            logger.exception(
                "Unexpected collection error chat=%s msg=%s", chat_id, message.id
            )


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
    collector = Collector(client, use_case, Path(config.storage.media_directory))
    try:
        await client.start()
        await collector.run(
            config.telegram.source_channels,
            startup_backfill_limit=config.telegram.collector_startup_backfill_limit,
        )
    finally:
        await client.disconnect()
        mongo_client.close()
        await db.close()


def main() -> None:
    """Synchronous entrypoint for ``python -m src.workers.collector``."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
