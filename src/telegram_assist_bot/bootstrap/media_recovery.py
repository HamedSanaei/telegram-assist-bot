"""Queue an exact rejected source message without acquiring a Telegram session."""

from __future__ import annotations

from typing import TYPE_CHECKING

from telegram_assist_bot.application.approvals import (
    AuthorizationStatus,
    AuthorizeAdminAction,
)
from telegram_assist_bot.application.ports.admin import BotUpdate
from telegram_assist_bot.application.retry_rejected_media import (
    request_rejected_media_retry,
)
from telegram_assist_bot.bootstrap.runtime import create_foundation_application
from telegram_assist_bot.bootstrap.text_ingestion import SystemClock
from telegram_assist_bot.domain import Administrator, AdminPermission
from telegram_assist_bot.domain.posts import SourceMessageIdentity
from telegram_assist_bot.infrastructure.persistence.mongodb.content_repository import (
    MongoContentPreparationRepository,
)

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from telegram_assist_bot.shared.observability import EventSink


async def queue_rejected_media_retry(
    configuration_path: Path,
    *,
    environ: Mapping[str, str],
    sink: EventSink,
    source_channel_id: int,
    source_message_id: int,
    administrator_id: int,
) -> bool:
    """Revalidate configured administrator/source permissions and queue recovery.

    This is a trusted local-operator CLI, not a public identity authentication
    endpoint. The configured actor must be active and allowed to toggle every
    enabled destination affected by the shared source-message operation.
    """
    application = create_foundation_application(sink=sink)
    try:
        await application.start(configuration_path, environ=environ)
        settings = application.configuration.settings
        clock = SystemClock()
        destinations = {
            item.name: item.telegram_channel_id
            for item in settings.destination_channels
            if item.enabled
        }
        administrators = tuple(
            Administrator(
                item.telegram_user_id,
                item.active,
                item.role,
                frozenset(AdminPermission(value) for value in item.permissions),
                frozenset(item.allowed_destination_ids)
                or frozenset(
                    destinations[name]
                    for name in item.allowed_destination_names
                    if name in destinations
                ),
            )
            for item in settings.admins
        )
        authorize = AuthorizeAdminAction(administrators)
        update = BotUpdate(administrator_id, administrator_id, "private")
        if authorize.execute(update, AdminPermission.TOGGLE).status is not (
            AuthorizationStatus.ALLOWED
        ):
            raise PermissionError("Media retry requires administrator permission.")
        post = await application.repository.get_by_source_identity(
            SourceMessageIdentity(source_channel_id, source_message_id),
            as_of=clock.utc_now(),
        )
        if post is None:
            raise ValueError("Rejected source Post does not exist.")
        source = next(
            (
                item
                for item in settings.source_channels
                if item.enabled
                and (
                    item.telegram_channel_id == source_channel_id
                    or item.username.removeprefix("@").strip().casefold()
                    == (post.source_channel_username or "")
                    .removeprefix("@")
                    .strip()
                    .casefold()
                )
            ),
            None,
        )
        if source is None:
            raise PermissionError("Media retry requires an enabled configured source.")
        affected = tuple(
            destinations[name]
            for name in source.allowed_destination_names
            if name in destinations
        )
        if not affected or any(
            authorize.execute(
                update, AdminPermission.TOGGLE, destination_id=destination_id
            ).status
            is not AuthorizationStatus.ALLOWED
            for destination_id in affected
        ):
            raise PermissionError("Media retry destination permission was denied.")
        database = application.mongodb_client[settings.mongodb.database_name]
        content = MongoContentPreparationRepository(
            database["media_items"],
            database["media_groups"],
            database["content_preparations"],
        )
        return await request_rejected_media_retry(
            source_channel_id=source_channel_id,
            source_message_id=source_message_id,
            update=update,
            authorize=authorize,
            post_repository=application.repository,
            content_repository=content,
            destination_ids=affected,
            clock=clock,
        )
    finally:
        await application.shutdown()
