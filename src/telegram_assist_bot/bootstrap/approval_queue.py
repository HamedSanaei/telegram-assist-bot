"""Safe approval-delivery queue inspection and explicit retry commands."""

from __future__ import annotations

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


__all__ = ("inspect_approval_queue", "retry_approval_delivery")
