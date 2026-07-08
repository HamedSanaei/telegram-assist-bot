"""Telethon adapters for native scheduled channel publishing and metadata."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, TypeVar

from telethon import TelegramClient, functions, types

from src.domain.entities import Post, PostSourceMetrics, TextEntity
from src.domain.enums import MediaKind
from src.shared.errors import TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_CAPTION_LIMIT = 1024
T = TypeVar("T")


def _is_disconnected_error(exc: Exception) -> bool:
    """Return whether an exception means the Telethon client disconnected."""
    return isinstance(exc, ConnectionError) or "while disconnected" in str(exc).lower()


async def _ensure_connected(client: TelegramClient) -> None:
    """
    Ensure a Telethon client is connected before issuing MTProto requests.

    Args:
        client: Telethon user client shared by publishing and metadata refresh.

    Side effects:
        Reconnects the client when Telethon reports it as disconnected.
    """
    is_connected = getattr(client, "is_connected", None)
    if callable(is_connected) and not is_connected():
        logger.warning("Telethon user session disconnected; reconnecting")
        await client.connect()


async def _run_with_reconnect(
    client: TelegramClient,
    operation: Callable[[], Awaitable[T]],
    context: str,
) -> T:
    """
    Run one Telethon operation, reconnecting once after disconnection.

    Args:
        client: Telethon user client.
        operation: Async callable that performs the MTProto request.
        context: Short log context describing the operation.

    Returns:
        The operation result.

    Raises:
        Exception: Re-raises the original operation error after one failed
        reconnect retry, or immediately for non-disconnection errors.
    """
    await _ensure_connected(client)
    try:
        return await operation()
    except Exception as exc:
        if not _is_disconnected_error(exc):
            raise
        logger.warning("Telethon request disconnected context=%s; reconnecting", context)
        await client.connect()
        return await operation()


class TelethonDestinationPublisher:
    """
    Publish destination posts via a Telegram user session.

    Bot API cannot create scheduled channel posts. This adapter therefore
    uses a logged-in Telethon user account that must be an admin of every
    destination channel. The same path is also used for immediate post
    publishing so premium custom emoji entities can be preserved with
    Telethon ``formatting_entities``.

    Example:
        publisher = TelethonDestinationPublisher(client)
        await publisher.publish_post(-100123, post)
        await publisher.schedule_post(-100123, post, scheduled_at)
    """

    def __init__(self, client: TelegramClient) -> None:
        """
        Args:
            client: Started Telethon user client with destination admin rights.
        """
        self._client = client

    async def publish_text(self, chat_id: int, text: str) -> int:
        """
        Publish a plain text message immediately.

        Args:
            chat_id: Destination channel chat id.
            text: Message text to publish.

        Returns:
            Telegram message id.

        Raises:
            TelegramPublishError: When Telegram rejects the send.
        """
        try:
            async def operation() -> object:
                entity = await self._client.get_entity(chat_id)
                return await self._client.send_message(entity, text)

            message = await _run_with_reconnect(
                self._client, operation, f"publish_text chat={chat_id}"
            )
            return int(getattr(message, "id", 0) or 0)
        except Exception as exc:
            raise TelegramPublishError(
                f"publish_text failed chat={chat_id}: {exc}"
            ) from exc

    async def publish_post(self, chat_id: int, post: Post) -> int:
        """
        Publish one post immediately using the destination user session.

        Args:
            chat_id: Destination channel chat id.
            post: Post with rewritten text/media/entities.

        Returns:
            Telegram message id of the published primary message.

        Raises:
            TelegramPublishError: When the Telethon send fails.
        """
        try:
            async def operation() -> object:
                entity = await self._client.get_entity(chat_id)
                return await self._send_post(entity, post, schedule=None)

            message = await _run_with_reconnect(
                self._client, operation, f"publish_post post={post.post_id} chat={chat_id}"
            )
            return int(getattr(message, "id", 0) or 0)
        except TelegramPublishError:
            raise
        except Exception as exc:
            raise TelegramPublishError(
                f"publish_post failed post={post.post_id} chat={chat_id}: {exc}"
            ) from exc

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """
        Delete an immediate destination-channel message.

        Args:
            chat_id: Destination channel chat id.
            message_id: Telegram message id to delete.

        Raises:
            TelegramPublishError: When Telegram rejects deletion.
        """
        try:
            async def operation() -> None:
                entity = await self._client.get_entity(chat_id)
                await self._client.delete_messages(entity, [message_id])

            await _run_with_reconnect(
                self._client,
                operation,
                f"delete_message chat={chat_id} message={message_id}",
            )
        except Exception as exc:
            raise TelegramPublishError(
                f"delete_message failed chat={chat_id} message={message_id}: {exc}"
            ) from exc

    async def latest_scheduled_at(self, chat_id: int) -> datetime | None:
        """
        Return the latest native Telegram scheduled message time if visible.

        Args:
            chat_id: Destination channel chat id.

        Returns:
            Latest scheduled UTC datetime, or ``None`` when Telegram has no
            scheduled messages or the lookup fails.
        """
        try:
            async def operation() -> object:
                entity = await self._client.get_entity(chat_id)
                return await self._client(
                    functions.messages.GetScheduledHistoryRequest(peer=entity, hash=0)
                )

            result = await _run_with_reconnect(
                self._client, operation, f"latest_scheduled_at chat={chat_id}"
            )
        except Exception as exc:
            logger.warning(
                "Telegram scheduled history lookup failed chat=%s error=%s",
                chat_id,
                exc,
            )
            return None
        dates = [
            self._as_utc(getattr(message, "date", None))
            for message in getattr(result, "messages", [])
        ]
        clean_dates = [value for value in dates if value is not None]
        return max(clean_dates) if clean_dates else None

    async def schedule_post(
        self, chat_id: int, post: Post, scheduled_at: datetime
    ) -> int:
        """
        Upload one post into Telegram's native channel schedule.

        Args:
            chat_id: Destination channel chat id.
            post: Post with rewritten text/media.
            scheduled_at: UTC schedule time.

        Returns:
            Telegram id of the scheduled message when available.

        Raises:
            TelegramPublishError: When the Telethon send fails.
        """
        try:
            scheduled_at = self._as_utc(scheduled_at) or datetime.now(timezone.utc)

            async def operation() -> object:
                entity = await self._client.get_entity(chat_id)
                return await self._send_post(entity, post, schedule=scheduled_at)

            message = await _run_with_reconnect(
                self._client, operation, f"schedule_post post={post.post_id} chat={chat_id}"
            )
            return int(getattr(message, "id", 0) or 0)
        except TelegramPublishError:
            raise
        except Exception as exc:
            raise TelegramPublishError(
                f"schedule_post failed post={post.post_id} chat={chat_id}: {exc}"
            ) from exc

    async def delete_scheduled_message(self, chat_id: int, message_id: int) -> None:
        """
        Delete a message from Telegram's native scheduled posts.

        Args:
            chat_id: Destination channel chat id.
            message_id: Scheduled Telegram message id.

        Raises:
            TelegramPublishError: When Telegram rejects deletion.
        """
        try:
            async def operation() -> None:
                entity = await self._client.get_entity(chat_id)
                await self._client(
                    functions.messages.DeleteScheduledMessagesRequest(
                        peer=entity,
                        id=[message_id],
                    )
                )

            await _run_with_reconnect(
                self._client,
                operation,
                f"delete_scheduled_message chat={chat_id} message={message_id}",
            )
        except Exception as exc:
            raise TelegramPublishError(
                "delete_scheduled_message failed "
                f"chat={chat_id} message={message_id}: {exc}"
            ) from exc

    async def _send_post(
        self, entity: object, post: Post, schedule: datetime | None
    ) -> object:
        """
        Send one post immediately or as a native scheduled message.

        Args:
            entity: Resolved Telethon destination entity.
            post: Post with text, media, and optional formatting entities.
            schedule: UTC schedule time, or ``None`` for immediate publishing.

        Returns:
            The primary Telethon message object.

        Raises:
            TelegramPublishError: When the post has no publishable content.
        """
        media = self._first_existing_media(post)
        text = post.text or ""
        formatting_entities = self._formatting_entities(post.text_entities)
        send_kwargs: dict[str, object] = {}
        if schedule is not None:
            send_kwargs["schedule"] = schedule
        if media is not None:
            path = str(media[1])
            if len(text) <= _CAPTION_LIMIT:
                kwargs = dict(send_kwargs)
                if formatting_entities and text:
                    kwargs["formatting_entities"] = formatting_entities
                return await self._client.send_file(
                    entity,
                    path,
                    caption=text or None,
                    **kwargs,
                )
            message = await self._client.send_file(entity, path, **send_kwargs)
            kwargs = dict(send_kwargs)
            if formatting_entities:
                kwargs["formatting_entities"] = formatting_entities
            await self._client.send_message(entity, text, **kwargs)
            return message
        if not text:
            raise TelegramPublishError(f"Post {post.post_id} has no publishable content")
        kwargs = dict(send_kwargs)
        if formatting_entities:
            kwargs["formatting_entities"] = formatting_entities
        return await self._client.send_message(entity, text, **kwargs)

    @staticmethod
    def _first_existing_media(post: Post) -> tuple[MediaKind, Path] | None:
        """Return the first existing media file in publish-preferred order."""
        preferred_order = (MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.DOCUMENT)
        for kind in preferred_order:
            for media in post.media:
                if media.kind != kind or not media.file_path:
                    continue
                path = Path(media.file_path)
                if path.exists():
                    return kind, path
        return None

    @staticmethod
    def _formatting_entities(
        entities: list[TextEntity],
    ) -> list[types.MessageEntityCustomEmoji]:
        """Convert stored custom emoji entities back to Telethon entities."""
        result: list[types.MessageEntityCustomEmoji] = []
        for entity in entities:
            if entity.kind != "custom_emoji":
                continue
            document_id = entity.data.get("document_id")
            if not isinstance(document_id, int):
                continue
            result.append(
                types.MessageEntityCustomEmoji(
                    offset=entity.offset,
                    length=entity.length,
                    document_id=document_id,
                )
            )
        return result

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        """Return a timezone-aware UTC datetime."""
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


TelethonScheduledPublisher = TelethonDestinationPublisher


class TelethonSourceMetadataRefresher:
    """
    Refreshes engagement metrics for source posts through Telethon.

    The same scheduler user session can be used when it can read source
    channels. If it cannot access a source, callers receive ``None`` and
    should continue with stored metrics.
    """

    def __init__(self, client: TelegramClient) -> None:
        """
        Args:
            client: Started Telethon client with read access to source posts.
        """
        self._client = client

    async def refresh_metrics(
        self, source_chat_id: int, source_message_id: int
    ) -> PostSourceMetrics | None:
        """
        Fetch the source message again and extract fresh metrics.

        Args:
            source_chat_id: Source Telegram channel chat id.
            source_message_id: Message id inside the source channel.

        Returns:
            Refreshed metrics, or ``None`` when unavailable.
        """
        try:
            async def operation() -> object:
                entity = await self._client.get_entity(source_chat_id)
                return await self._client.get_messages(entity, ids=source_message_id)

            message = await _run_with_reconnect(
                self._client,
                operation,
                f"refresh_metrics chat={source_chat_id} msg={source_message_id}",
            )
        except Exception as exc:
            logger.warning(
                "Source message metadata fetch failed chat=%s msg=%s error=%s",
                source_chat_id,
                source_message_id,
                exc,
            )
            return None
        if message is None:
            return None
        return PostSourceMetrics(
            views=self._int_or_none(getattr(message, "views", None)),
            forwards=self._int_or_none(getattr(message, "forwards", None)),
            replies_count=self._reply_count(message),
            reactions_count=self._reaction_count(getattr(message, "reactions", None)),
            source_published_at=self._message_date(message),
        )

    @staticmethod
    def _int_or_none(value: object) -> int | None:
        """Return an integer value, or ``None`` when unavailable."""
        return value if isinstance(value, int) else None

    @staticmethod
    def _reply_count(message: object) -> int | None:
        """Return the reply/comment count when available."""
        replies = getattr(message, "replies", None)
        return TelethonSourceMetadataRefresher._int_or_none(
            getattr(replies, "replies", None)
        )

    @staticmethod
    def _reaction_count(reactions: object) -> int | None:
        """Return the summed Telegram reaction count when available."""
        results = getattr(reactions, "results", None)
        if not results:
            return None
        total = 0
        found = False
        for item in results:
            count = getattr(item, "count", None)
            if isinstance(count, int):
                total += count
                found = True
        return total if found else None

    @staticmethod
    def _message_date(message: object) -> datetime | None:
        """Return a Telethon message date as aware UTC, if available."""
        value = getattr(message, "date", None)
        if not isinstance(value, datetime):
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
