"""Read-only publication queue inspection and explicit idempotent cancellation."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from uuid import uuid4

from telegram_assist_bot.application.scheduling import (
    CancelRequest,
    CancelScheduledPost,
)
from telegram_assist_bot.bootstrap.runtime import create_foundation_application
from telegram_assist_bot.domain import CancellationPolicy, ScheduleStatus
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoScheduleRepository,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from telegram_assist_bot.domain import CancellationResult
    from telegram_assist_bot.shared.observability import EventSink


async def inspect_publication_queue(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    status: str,
) -> tuple[str, ...]:
    """Return safe queue rows without loading publication payload or media."""
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        requested = ScheduleStatus(status.capitalize()).value
        destinations = {
            item.telegram_channel_id: item.name
            for item in settings.destination_channels
        }
        rows: list[str] = []
        cursor = (
            database["scheduled_publications"]
            .find(
                {"status": requested},
                projection={
                    "post_id": 1,
                    "destination_id": 1,
                    "action": 1,
                    "status": 1,
                    "due_at": 1,
                    "attempt_count": 1,
                },
            )
            .sort([("due_at", 1), ("_id", 1)])
        )
        async for job in cursor:
            destination_id = cast("int", job["destination_id"])
            due_at = cast("datetime", job["due_at"])
            post = await database["posts"].find_one(
                {"_id": job["post_id"]}, projection={"source_message_id": 1}
            )
            rows.append(
                " | ".join(
                    (
                        f"job_id={job['_id']}",
                        f"post_id={str(job['post_id'])[:12]}",
                        "source_message_id="
                        f"{(post or {}).get('source_message_id', 'unknown')}",
                        f"destination={destinations.get(destination_id, 'unknown')}",
                        f"action={job.get('action', 'scheduled')}",
                        f"status={job['status']}",
                        f"due_at={due_at.astimezone(UTC).isoformat()}",
                        f"attempt_count={job.get('attempt_count', 0)}",
                    )
                )
            )
        return tuple(rows)
    finally:
        await application.shutdown()


async def cancel_publication_job(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    job_id: str,
) -> CancellationResult:
    """Cancel exactly one named durable job through the existing policy."""
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        repository = MongoScheduleRepository(
            database["scheduled_publications"], database["schedule_queues"]
        )
        job = await repository.get(job_id)
        if job is None:
            from telegram_assist_bot.domain import CancellationResult

            return CancellationResult.NOT_FOUND
        cancel = CancelScheduledPost(
            repository,
            clock=lambda: datetime.now(UTC),
            interval_seconds=settings.publishing.scheduled_publication_interval_seconds,
            policy=CancellationPolicy(settings.publishing.cancellation_policy),
        )
        return await cancel.execute(
            CancelRequest(job_id, job.destination_id, job.version, 0, uuid4().hex, True)
        )
    finally:
        await application.shutdown()


__all__ = ("cancel_publication_job", "inspect_publication_queue")
