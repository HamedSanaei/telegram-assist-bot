"""Read-only publication queue inspection and explicit idempotent cancellation."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast
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

    from pymongo.asynchronous.database import AsyncDatabase

    from telegram_assist_bot.domain import CancellationResult
    from telegram_assist_bot.shared.observability import EventSink


class PreSendRecoveryResult(StrEnum):
    """Describe one narrowly proven immediate-publication recovery."""

    REQUEUED = "requeued"
    ALREADY_REQUEUED = "already_requeued"
    NOT_ELIGIBLE = "not_eligible"


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


async def recover_pre_send_publication(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    approval_post_id: str,
) -> PreSendRecoveryResult:
    """Requeue one exact legacy text-URL failure proven to be pre-send."""
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        return await _recover_pre_send_in_database(
            database,
            approval_post_id=approval_post_id,
            now=datetime.now(UTC),
        )
    finally:
        await application.shutdown()


async def _recover_pre_send_in_database(
    database: AsyncDatabase[dict[str, Any]],
    *,
    approval_post_id: str,
    now: datetime,
) -> PreSendRecoveryResult:
    """Apply the proof gate without loading or emitting post text or URLs."""
    if not approval_post_id or approval_post_id.isspace() or now.tzinfo is None:
        raise ValueError("An exact Post ID and aware recovery time are required.")
    preparations = database["content_preparations"]
    schedules = database["scheduled_publications"]
    publications = database["publications"]
    preparation = await preparations.find_one(
        {"_id": approval_post_id}, projection={"artifacts": 1}
    )
    artifacts = (preparation or {}).get("artifacts", {})
    missing_text_url = any(
        entity.get("entity_type") == "text_url" and not entity.get("url")
        for artifact in artifacts.values()
        for entity in artifact.get("entities", ())
    )
    if not missing_text_url:
        return PreSendRecoveryResult.NOT_ELIGIBLE
    schedule = await schedules.find_one(
        {
            "post_id": approval_post_id,
            "action": "immediate",
        },
        projection={"_id": 1, "status": 1, "last_error_category": 1},
    )
    if schedule is None:
        return PreSendRecoveryResult.NOT_ELIGIBLE
    if schedule.get("status") == "Pending":
        return (
            PreSendRecoveryResult.ALREADY_REQUEUED
            if schedule.get("last_error_category") == "recovered_pre_send"
            else PreSendRecoveryResult.NOT_ELIGIBLE
        )
    publication = await publications.find_one(
        {
            "_id": schedule["_id"],
            "post_id": approval_post_id,
            "action": "immediate",
            "state": "Claimed",
            "lease_until": {"$lte": now.astimezone(UTC)},
            "published_at": {"$exists": False},
            "message_ids": {"$exists": False},
        },
        projection={"_id": 1},
    )
    if publication is None:
        return PreSendRecoveryResult.NOT_ELIGIBLE
    result = await schedules.update_one(
        {
            "_id": schedule["_id"],
            "post_id": approval_post_id,
            "action": "immediate",
            "status": "OutcomeUnknown",
            "last_error_category": "ambiguous",
            "last_failure_type": "ValueError",
        },
        {
            "$set": {
                "status": "Pending",
                "claim_owner": None,
                "lease_until": None,
                "next_attempt_at": None,
                "last_error_category": "recovered_pre_send",
                "last_failure_type": None,
            },
            "$inc": {"version": 1},
        },
    )
    return (
        PreSendRecoveryResult.REQUEUED
        if result.modified_count == 1
        else PreSendRecoveryResult.NOT_ELIGIBLE
    )


__all__ = (
    "PreSendRecoveryResult",
    "cancel_publication_job",
    "inspect_publication_queue",
    "recover_pre_send_publication",
)
