"""Pure application mapping from source DTOs to stored post snapshots."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from telegram_assist_bot.domain.posts import (
    OriginalPostContent,
    Post,
    PostId,
    PostStatus,
    SourceMessageIdentity,
    TransitionActorCategory,
)

if TYPE_CHECKING:
    from datetime import datetime

    from telegram_assist_bot.application.ports import TelegramTextMessage


class PostIdFactory(Protocol):
    """Create one application-owned post identifier for a source identity."""

    def __call__(self, identity: SourceMessageIdentity, /) -> PostId:
        """Return one non-database-specific post identifier."""
        ...


def build_stored_post(
    message: TelegramTextMessage,
    *,
    received_at: datetime,
    post_id_factory: PostIdFactory,
) -> Post:
    """Build one exact original-content snapshot already transitioned to Stored."""
    identity = SourceMessageIdentity(
        source_channel_id=message.source_channel_id,
        source_message_id=message.source_message_id,
    )
    discovered = Post(
        post_id=post_id_factory(identity),
        source_identity=identity,
        source_channel_username=message.source_channel_username,
        source_channel_display_name=message.source_channel_display_name,
        original_content=OriginalPostContent(
            text=message.text,
            caption=message.caption,
            text_entities=message.text_entities,
            caption_entities=message.caption_entities,
        ),
        source_published_at=message.source_published_at,
        received_at=received_at,
    )
    return discovered.transition_to(
        PostStatus.STORED,
        expected_version=0,
        occurred_at=received_at,
        actor_category=TransitionActorCategory.SERVICE,
        reason="initial_ingestion",
    )


__all__ = ("PostIdFactory", "build_stored_post")
