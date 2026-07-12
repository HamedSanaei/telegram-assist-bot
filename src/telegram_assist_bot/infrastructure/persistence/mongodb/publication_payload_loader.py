"""Load destination-ready publication payloads from Milestone 2 persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from telegram_assist_bot.application.ports import (
    PublicationMedia,
    PublicationPayload,
)
from telegram_assist_bot.domain.media import MediaType
from telegram_assist_bot.domain.posts import PostId

if TYPE_CHECKING:
    from pymongo.asynchronous.collection import AsyncCollection

    from telegram_assist_bot.application.ports import ContentPreparationRepository

type Document = dict[str, Any]


class MongoPublicationPayloadLoader:
    """Join prepared artifacts to private single-media or finalized Album metadata."""

    def __init__(
        self,
        preparation_repository: ContentPreparationRepository,
        posts: AsyncCollection[Document],
        media: AsyncCollection[Document],
        groups: AsyncCollection[Document],
        *,
        destination_names: dict[int, str],
    ) -> None:
        """Store existing Milestone 2 collections and canonical destination names."""
        self._preparations = preparation_repository
        self._posts = posts
        self._media = media
        self._groups = groups
        self._destination_names = dict(destination_names)

    async def load(self, post_id: str, destination_id: int) -> PublicationPayload:
        """Return exact artifact text/entities plus ordered ready media metadata."""
        destination_name = self._destination_names.get(destination_id)
        if destination_name is None:
            raise ValueError("Publication destination is not configured.")
        artifact = await self._preparations.get_destination_artifact(
            PostId(post_id), destination_name
        )
        if artifact is None:
            raise ValueError("Prepared destination artifact does not exist.")
        post = await self._posts.find_one(
            {"_id": post_id},
            projection={"source_channel_id": 1, "source_message_id": 1},
        )
        if post is None:
            raise ValueError("Publication Post does not exist.")
        source_filter = {
            "source_channel_id": post["source_channel_id"],
            "source_message_id": post["source_message_id"],
        }
        group = await self._groups.find_one(
            {
                "source_channel_id": post["source_channel_id"],
                "members.source_message_id": post["source_message_id"],
                "finalized_at": {"$ne": None},
            }
        )
        documents: list[Document]
        if group is not None:
            documents = [item["media"] for item in group.get("members", ())]
        else:
            cursor = self._media.find({**source_filter, "cleaned_at": None}).sort(
                "item_index", 1
            )
            documents = [item async for item in cursor]
        media = tuple(
            PublicationMedia(
                MediaType(item["media_type"]),
                item["storage_path"],
                item["expires_at"],
                ready=item.get("cleaned_at") is None,
            )
            for item in documents
        )
        return PublicationPayload(
            destination_id, artifact.text, artifact.entities, media
        )


__all__ = ("MongoPublicationPayloadLoader",)
