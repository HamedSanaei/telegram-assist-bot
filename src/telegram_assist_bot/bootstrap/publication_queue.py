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
    DRY_RUN_ELIGIBLE = "dry_run_eligible"
    CLEARED = "cleared"


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
    dry_run: bool = False,
    requeue: bool = True,
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
            dry_run=dry_run,
            requeue=requeue,
        )
    finally:
        await application.shutdown()


async def _recover_pre_send_in_database(
    database: AsyncDatabase[dict[str, Any]],
    *,
    approval_post_id: str,
    now: datetime,
    dry_run: bool = False,
    requeue: bool = True,
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


async def recover_failed_immediate_selection(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    approval_post_id: str,
    dry_run: bool,
    requeue: bool,
) -> PreSendRecoveryResult:
    """Clear or requeue one exact BSON-int pre-send immediate failure safely."""
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        return await _recover_failed_immediate_in_database(
            database,
            approval_post_id=approval_post_id,
            now=datetime.now(UTC),
            dry_run=dry_run,
            requeue=requeue,
        )
    finally:
        await application.shutdown()


async def _recover_failed_immediate_in_database(
    database: AsyncDatabase[dict[str, Any]],
    *,
    approval_post_id: str,
    now: datetime,
    dry_run: bool,
    requeue: bool,
) -> PreSendRecoveryResult:
    """Require exact terminal pre-send proof before changing durable state."""
    schedule = await database["scheduled_publications"].find_one(
        {
            "post_id": approval_post_id,
            "action": "immediate",
            "status": "PermanentFailed",
            "last_error_category": "permanent",
            "last_failure_type": "PublisherError",
        }
    )
    if schedule is None:
        return PreSendRecoveryResult.NOT_ELIGIBLE
    destination_id = schedule.get("destination_id")
    publication = await database["publications"].find_one(
        {
            "_id": schedule["_id"],
            "post_id": approval_post_id,
            "state": "PermanentFailed",
            "error_category": "permanent",
            "failure_type": "PublisherError",
            "published_at": {"$exists": False},
            "$or": [{"message_ids": {"$exists": False}}, {"message_ids": []}],
        }
    )
    selection = await database["destination_selections"].find_one(
        {
            "post_id": approval_post_id,
            "destination_id": destination_id,
            "mode": "immediate",
        }
    )
    legacy_bson_int_failure = (
        isinstance(destination_id, int)
        and not isinstance(destination_id, bool)
        and type(destination_id) is not int
        and schedule.get("last_failure_reason_code") is None
        and publication is not None
        and publication.get("failure_reason_code") is None
    )
    reason_proves_pre_send = (
        schedule.get("last_failure_reason_code") == "invalid_publication_payload"
        and publication is not None
        and publication.get("failure_reason_code") == "invalid_publication_payload"
    )
    if (
        publication is None
        or selection is None
        or not (legacy_bson_int_failure or reason_proves_pre_send)
    ):
        return PreSendRecoveryResult.NOT_ELIGIBLE
    normalized_destination_id = cast("int", destination_id)
    if dry_run:
        return PreSendRecoveryResult.DRY_RUN_ELIGIBLE
    if requeue:
        publication_result = await database["publications"].update_one(
            {"_id": schedule["_id"], "state": "PermanentFailed"},
            {
                "$set": {
                    "state": "Pending",
                    "claim_owner": None,
                    "lease_until": None,
                    "next_attempt_at": None,
                    "error_category": None,
                    "failure_type": None,
                    "failure_reason_code": None,
                },
                "$inc": {"version": 1},
            },
        )
        if publication_result.modified_count != 1:
            return PreSendRecoveryResult.NOT_ELIGIBLE
        await database["scheduled_publications"].update_one(
            {"_id": schedule["_id"], "status": "PermanentFailed"},
            {
                "$set": {
                    "status": "Pending",
                    "claim_owner": None,
                    "lease_until": None,
                    "next_attempt_at": None,
                    "last_error_category": None,
                    "last_failure_type": None,
                    "last_failure_reason_code": None,
                },
                "$inc": {"version": 1},
            },
        )
        status = "immediate_queued"
        result = PreSendRecoveryResult.REQUEUED
    else:
        selection_result = await database["destination_selections"].update_one(
            {"_id": selection["_id"], "version": selection.get("version", 0)},
            {
                "$set": {"mode": "none"},
                "$inc": {"version": 1},
                "$push": {
                    "history": {
                        "actor_id": 0,
                        "previous": "immediate",
                        "current": "none",
                        "occurred_at": now,
                        "correlation_id": uuid4().hex,
                    }
                },
            },
        )
        if selection_result.modified_count != 1:
            return PreSendRecoveryResult.NOT_ELIGIBLE
        status = "cancelled"
        result = PreSendRecoveryResult.CLEARED
    key = f"destination_statuses.{int(normalized_destination_id)}"
    await database["approval_deliveries"].update_one(
        {"_id": approval_post_id},
        {
            "$set": {
                f"{key}.status": status,
                f"{key}.version": selection.get("version", 0) + (0 if requeue else 1),
                f"{key}.updated_at": now,
                f"{key}.action": "immediate" if requeue else None,
                f"{key}.due_at": schedule.get("due_at") if requeue else None,
                "sync_required": True,
            },
            "$inc": {"sync_version": 1},
        },
    )
    return result


__all__ = (
    "PreSendRecoveryResult",
    "cancel_publication_job",
    "inspect_publication_queue",
    "recover_failed_immediate_selection",
    "recover_pre_send_publication",
)
