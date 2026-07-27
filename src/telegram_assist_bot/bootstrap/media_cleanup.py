"""One-shot Composition Root for bounded private-media cleanup."""

from __future__ import annotations

import asyncio
import signal
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from telegram_assist_bot.application.cleanup_expired_media import CleanupExpiredMedia
from telegram_assist_bot.bootstrap.runtime import (
    FoundationApplication,
    FoundationExitCode,
    FoundationInfrastructureError,
    FoundationStartupError,
    create_foundation_application,
)
from telegram_assist_bot.infrastructure.media import LocalMediaStorage
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
    MongoMediaReferenceCollections,
    initialize_content_preparation_indexes,
)
from telegram_assist_bot.shared.config import ApplicationConfig, LogLevel
from telegram_assist_bot.workers.media_cleanup import PeriodicMediaCleanupWorker

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from telegram_assist_bot.application.ports import MediaOperationError
    from telegram_assist_bot.domain.media import StoredMedia
    from telegram_assist_bot.shared.observability import EventSink


class _SystemClock:
    """Provide current UTC time to the periodic application-friendly worker."""

    def utc_now(self) -> datetime:
        """Return the current aware UTC instant."""
        return datetime.now(UTC)


async def _build_cleanup_use_case(
    foundation: FoundationApplication,
) -> tuple[CleanupExpiredMedia, ApplicationConfig]:
    """Build cleanup boundaries from one already-started Foundation lifecycle."""
    settings = foundation.configuration.settings
    database = foundation.mongodb_client[settings.mongodb.database_name]
    media = database["media_items"]
    groups = database["media_groups"]
    preparations = database["content_preparations"]
    await initialize_content_preparation_indexes(media, groups, preparations)
    references = MongoMediaReferenceCollections(
        posts=database["posts"],
        publications=database["publications"],
        schedules=database["scheduled_publications"],
        native_schedules=database["native_schedule_commands"],
        approval_deliveries=database["approval_deliveries"],
        advertisement_sources=database["advertisement_sources"],
        advertisement_slots=database["advertisement_slots"],
    )
    repository = MongoContentPreparationRepository(
        media, groups, preparations, references=references
    )

    def report_candidate_error(
        item: StoredMedia | None, error: MediaOperationError, attempt: int
    ) -> None:
        fields: dict[str, object] = {"retry_attempt": attempt}
        if item is not None:
            fields.update(
                {
                    "source_channel_id": item.identity.source_channel_id,
                    "source_message_id": item.identity.source_message_id,
                }
            )
        foundation.logger.emit(
            level=LogLevel.WARNING,
            event_name="media_cleanup_candidate_failed",
            fields=fields,
            error=error,
        )

    use_case = CleanupExpiredMedia(
        repository,
        LocalMediaStorage(settings.media.root),
        orphan_grace=timedelta(seconds=settings.media.orphan_grace_seconds),
        batch_size=settings.media.cleanup_batch_size,
        on_candidate_error=report_candidate_error,
    )
    return use_case, settings


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
        use_case, _settings = await _build_cleanup_use_case(foundation)
        cleaned = await use_case.execute(now=datetime.now(UTC))
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


async def run_media_cleanup_worker(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    stop_event: asyncio.Event | None = None,
) -> FoundationExitCode:
    """Run periodic bounded cleanup and close MongoDB on every outcome."""
    foundation = create_foundation_application(sink=sink)
    owned_stop = stop_event is None
    shutdown = stop_event or asyncio.Event()
    registered: list[signal.Signals] = []
    try:
        await foundation.start(configuration_path, environ=environ)
        use_case, settings = await _build_cleanup_use_case(foundation)
        if owned_stop:
            loop = asyncio.get_running_loop()
            for signum in (signal.SIGINT, signal.SIGTERM):
                try:
                    loop.add_signal_handler(signum, shutdown.set)
                except (NotImplementedError, RuntimeError, ValueError):
                    continue
                registered.append(signum)
        iterations = await PeriodicMediaCleanupWorker(
            use_case,
            _SystemClock(),
            interval_seconds=settings.media.cleanup_interval_seconds,
        ).run(shutdown)
        foundation.logger.emit(
            level=LogLevel.INFO,
            event_name="media_cleanup_worker_stopped",
            fields={"completed_iteration_count": iterations},
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
            event_name="media_cleanup_worker_failed",
            error=FoundationInfrastructureError(cause=error),
        )
        await foundation.shutdown()
        return FoundationExitCode.INFRASTRUCTURE_ERROR
    finally:
        if owned_stop:
            loop = asyncio.get_running_loop()
            for signum in registered:
                loop.remove_signal_handler(signum)
    await foundation.shutdown()
    return FoundationExitCode.SUCCESS


__all__ = ("run_media_cleanup", "run_media_cleanup_worker")
