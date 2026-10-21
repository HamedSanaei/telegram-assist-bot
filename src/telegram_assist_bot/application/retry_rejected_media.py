"""Authorize explicit durable media rejection recovery without opening Telegram."""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from telegram_assist_bot.application.approvals import AuthorizationStatus
from telegram_assist_bot.application.ports.media import MediaTransientError
from telegram_assist_bot.domain.admin_approval import AdminPermission
from telegram_assist_bot.domain.posts import SourceMessageIdentity

if TYPE_CHECKING:
    from telegram_assist_bot.application.approvals import AuthorizeAdminAction
    from telegram_assist_bot.application.ports import Clock, PostRepository
    from telegram_assist_bot.application.ports.admin import BotUpdate
    from telegram_assist_bot.application.ports.media import ContentPreparationRepository


async def request_rejected_media_retry(
    *,
    source_channel_id: int,
    source_message_id: int,
    update: BotUpdate,
    authorize: AuthorizeAdminAction,
    post_repository: PostRepository,
    content_repository: ContentPreparationRepository,
    destination_ids: tuple[int, ...] = (),
    clock: Clock,
) -> bool:
    """Recheck administrator policy and reopen rejection under the runtime lease."""
    decision = authorize.execute(update, AdminPermission.TOGGLE)
    if decision.status is not AuthorizationStatus.ALLOWED:
        raise PermissionError("Media retry requires administrator permission.")
    for destination_id in destination_ids:
        if (
            authorize.execute(
                update, AdminPermission.TOGGLE, destination_id=destination_id
            ).status
            is not AuthorizationStatus.ALLOWED
        ):
            raise PermissionError("Media retry destination permission was denied.")
    now = clock.utc_now()
    post = await post_repository.get_by_source_identity(
        SourceMessageIdentity(source_channel_id, source_message_id), as_of=now
    )
    if post is None:
        raise ValueError("Rejected source Post does not exist.")
    owner = uuid4().hex
    if not await content_repository.claim_ingestion(
        post.post_id, owner=owner, now=now, lease_until=now + timedelta(minutes=5)
    ):
        raise MediaTransientError("Media retry conflicts with active ingestion.")
    try:
        return await content_repository.reset_media_rejections(
            post.post_id, administrator_id=update.actor_id, at=now
        )
    finally:
        await content_repository.release_ingestion(post.post_id, owner=owner)
