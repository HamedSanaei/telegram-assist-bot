"""Safe approval-delivery queue inspection and explicit retry commands."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, cast

from telegram_assist_bot.bootstrap.runtime import create_foundation_application
from telegram_assist_bot.infrastructure.persistence.mongodb import (
    MongoOperationalApprovalRepository,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from pymongo.asynchronous.database import AsyncDatabase

    from telegram_assist_bot.shared.observability import EventSink


@dataclass(frozen=True, slots=True)
class ApprovalDocumentRecoveryResult:
    """Summarize one bounded operator recovery without exposing content."""

    matching_post_ids: tuple[str, ...]
    requeued_post_ids: tuple[str, ...]


async def inspect_approval_queue(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    status: str,
) -> tuple[str, ...]:
    """Return safe per-administrator rows without loading content or media paths."""
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        requested = status.replace("-", "_")
        rows: list[str] = []
        cursor = (
            database["approval_deliveries"]
            .find({})
            .sort([("claim_due_at", 1), ("created_at", 1), ("_id", 1)])
        )
        async for delivery in cursor:
            post_id = str(delivery["_id"])
            post = await database["posts"].find_one(
                {"_id": post_id},
                projection={"source_channel_id": 1, "source_message_id": 1},
            )
            content_kind = await _content_kind(database, post)
            states = cast(
                "dict[str, dict[str, Any]]",
                delivery.get("administrator_deliveries", {}),
            )
            for admin in settings.admins:
                reference = await database["approval_references"].find_one(
                    {"_id": f"approval:{post_id}:{admin.telegram_user_id}"},
                    projection={"active": 1, "delivery_state": 1},
                )
                state = states.get(str(admin.telegram_user_id), {})
                row_status = _administrator_status(delivery, reference, state)
                if row_status != requested:
                    continue
                reference_phase = (reference or {}).get("delivery_state", "pending")
                delivery_phase = state.get("delivery_phase", reference_phase)
                attempt_count = state.get(
                    "attempt_count", delivery.get("attempt_count", 0)
                )
                next_attempt_at = state.get(
                    "next_attempt_at", delivery.get("next_attempt_at")
                )
                failure_type = (
                    state.get("failure_type", delivery.get("last_failure_type", "none"))
                    or "none"
                )
                rows.append(
                    " | ".join(
                        (
                            f"approval_post_id={post_id[:12]}",
                            "source_message_id="
                            f"{(post or {}).get('source_message_id', 'unknown')}",
                            f"content_kind={content_kind}",
                            f"delivery_phase={delivery_phase}",
                            f"administrator_id={admin.telegram_user_id}",
                            f"status={row_status.replace('_', '-')}",
                            f"attempt_count={attempt_count}",
                            f"next_attempt_at={_safe_time(next_attempt_at)}",
                            f"failure_type={failure_type}",
                        )
                    )
                )
        return tuple(rows)
    finally:
        await application.shutdown()


async def retry_approval_delivery(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    approval_post_id: str,
) -> bool:
    """Idempotently requeue one exact failed proposal while preserving successes."""
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        repository = MongoOperationalApprovalRepository(
            database["content_preparations"],
            database["approval_deliveries"],
            max_attempts=settings.telegram.bot.approval_retry_max_attempts,
        )
        return await repository.retry_delivery(approval_post_id, now=datetime.now(UTC))
    finally:
        await application.shutdown()


async def recover_rejected_document_deliveries(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    approval_post_id: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    dry_run: bool,
    limit: int,
) -> ApprovalDocumentRecoveryResult:
    """Requeue only bounded document-preview media rejections."""
    if not 1 <= limit <= 100:
        raise ValueError("Recovery limit must be between 1 and 100.")
    exact = approval_post_id is not None
    bounded_range = started_at is not None and ended_at is not None
    if exact == bounded_range:
        raise ValueError("Use an exact Post ID or one bounded time range.")
    if bounded_range:
        if started_at is None or ended_at is None:
            raise ValueError("Both recovery timestamps are required.")
        if (
            started_at.tzinfo is None
            or ended_at.tzinfo is None
            or started_at >= ended_at
        ):
            raise ValueError("Recovery timestamps must form an aware time range.")

    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        database = application.mongodb_client[settings.mongodb.database_name]
        return await _recover_rejected_documents_in_database(
            database,
            approval_post_id=approval_post_id,
            started_at=started_at,
            ended_at=ended_at,
            dry_run=dry_run,
            limit=limit,
            now=datetime.now(UTC),
        )
    finally:
        await application.shutdown()


async def _recover_rejected_documents_in_database(
    database: AsyncDatabase[dict[str, Any]],
    *,
    approval_post_id: str | None,
    started_at: datetime | None,
    ended_at: datetime | None,
    dry_run: bool,
    limit: int,
    now: datetime,
) -> ApprovalDocumentRecoveryResult:
    query: dict[str, Any] = {
        "status": "permanent_failed",
        "$expr": {
            "$anyElementTrue": {
                "$map": {
                    "input": {
                        "$objectToArray": {"$ifNull": ["$administrator_deliveries", {}]}
                    },
                    "as": "delivery",
                    "in": {
                        "$and": [
                            {"$eq": ["$$delivery.v.status", "permanent_failed"]},
                            {
                                "$eq": [
                                    "$$delivery.v.failure_category",
                                    "media_rejected",
                                ]
                            },
                        ]
                    },
                }
            }
        },
    }
    if approval_post_id is not None:
        query["_id"] = approval_post_id
    else:
        if started_at is None or ended_at is None:
            raise ValueError("Both recovery timestamps are required.")
        query["created_at"] = {
            "$gte": started_at.astimezone(UTC),
            "$lt": ended_at.astimezone(UTC),
        }

    matching: list[str] = []
    requeued: list[str] = []
    cursor = (
        database["approval_deliveries"]
        .find(query)
        .sort([("created_at", 1), ("_id", 1)])
    )
    async for delivery in cursor:
        post_id = str(delivery["_id"])
        post = await database["posts"].find_one(
            {"_id": post_id},
            projection={"source_channel_id": 1, "source_message_id": 1},
        )
        if await _content_kind(database, post) != "document":
            continue
        matching.append(post_id)
        if not dry_run and await _requeue_document_delivery(
            database, delivery, now=now
        ):
            requeued.append(post_id)
        if len(matching) >= limit:
            break
    return ApprovalDocumentRecoveryResult(tuple(matching), tuple(requeued))


async def _requeue_document_delivery(
    database: AsyncDatabase[dict[str, Any]],
    delivery: dict[str, Any],
    *,
    now: datetime,
) -> bool:
    states = cast(
        "dict[str, dict[str, Any]]", delivery.get("administrator_deliveries", {})
    )
    failed_identifiers = tuple(
        identifier
        for identifier, state in states.items()
        if state.get("status") == "permanent_failed"
        and state.get("failure_category") == "media_rejected"
    )
    if not failed_identifiers:
        return False
    required: dict[str, object] = {
        "_id": delivery["_id"],
        "status": "permanent_failed",
    }
    updates: dict[str, object] = {
        "status": "retry",
        "next_attempt_at": now,
        "claim_due_at": now,
        "operator_requeued_at": now,
    }
    for identifier in failed_identifiers:
        prefix = f"administrator_deliveries.{identifier}"
        required[f"{prefix}.status"] = "permanent_failed"
        required[f"{prefix}.failure_category"] = "media_rejected"
        updates[f"{prefix}.status"] = "retry"
        updates[f"{prefix}.attempt_count"] = 0
        updates[f"{prefix}.next_attempt_at"] = now
    result = await database["approval_deliveries"].update_one(
        required, {"$set": updates}
    )
    return result.modified_count == 1


def _administrator_status(
    delivery: dict[str, Any],
    reference: dict[str, Any] | None,
    state: dict[str, Any],
) -> str:
    if reference is not None and reference.get("active"):
        return "completed"
    if state:
        return str(state.get("status", "pending"))
    return str(delivery.get("status", "pending"))


async def _content_kind(
    database: AsyncDatabase[dict[str, Any]], post: dict[str, Any] | None
) -> str:
    if post is None:
        return "unknown"
    identity = {
        "source_channel_id": post.get("source_channel_id"),
        "source_message_id": post.get("source_message_id"),
    }
    group = await database["media_groups"].find_one(
        {
            "source_channel_id": identity["source_channel_id"],
            "members.source_message_id": identity["source_message_id"],
        },
        projection={"members": 1},
    )
    if group is not None and len(group.get("members", ())) > 1:
        return "album"
    media = await database["media_items"].find_one(
        identity, projection={"media_type": 1}
    )
    return str((media or {}).get("media_type", "text")).lower()


def _safe_time(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return "none"


__all__ = (
    "ApprovalDocumentRecoveryResult",
    "inspect_approval_queue",
    "recover_rejected_document_deliveries",
    "retry_approval_delivery",
)
