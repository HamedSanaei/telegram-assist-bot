"""One-shot Composition Root for bounded private-media cleanup."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.bootstrap.runtime import (
    FoundationExitCode,
    FoundationInfrastructureError,
    FoundationStartupError,
    create_foundation_application,
)
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.shared.config import LogLevel

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from telegram_assist_bot.shared.observability import EventSink


async def run_media_cleanup(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
) -> FoundationExitCode:
    """Run one configured cleanup batch and close MongoDB on every outcome."""
    foundation = create_foundation_application(sink=sink)
    try:
        await foundation.start(configuration_path, environ=environ)
        settings = foundation.configuration.settings
        database = foundation.mongodb_client[settings.mongodb.database_name]
        media = database["media_items"]
        groups = database["media_groups"]
        preparations = database["content_preparations"]
        await initialize_content_preparation_indexes(media, groups, preparations)
        repository = MongoContentPreparationRepository(media, groups, preparations)
        cleaned = await CleanupExpiredMedia(
            repository,
            LocalMediaStorage(settings.media.root),
            orphan_grace=timedelta(seconds=settings.media.orphan_grace_seconds),
            batch_size=settings.media.cleanup_batch_size,
        ).execute(now=datetime.now(UTC))
        foundation.logger.emit(
            level=LogLevel.INFO,
            event_name="media_cleanup_completed",
            fields={"cleaned_item_count": cleaned},
        )
    except asyncio.CancelledError:
        await foundation.shutdown()
        raise
    except FoundationStartupError as error:
        await foundation.shutdown()
        return error.exit_code
    except Exception as error:  # noqa: BLE001 - safe CLI infrastructure boundary.
        foundation.logger.emit(
            level=LogLevel.ERROR,
            event_name="media_cleanup_failed",
            error=FoundationInfrastructureError(cause=error),
        )
        await foundation.shutdown()
        return FoundationExitCode.INFRASTRUCTURE_ERROR
    await foundation.shutdown()
    return FoundationExitCode.SUCCESS


__all__ = ("run_media_cleanup",)
