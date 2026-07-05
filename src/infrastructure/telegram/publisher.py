"""Telegram publisher built on the aiogram Bot API client."""

from __future__ import annotations

from pathlib import Path

from aiogram import Bot
from aiogram.types import FSInputFile

from src.domain.entities import Post
from src.domain.enums import MediaKind
from src.shared.errors import TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_CAPTION_LIMIT = 1024


class AiogramMessagePublisher:
    """
    Implements :class:`MessagePublisher` using the main publishing bot.

    Notes:
        When a post has multiple photos, the first photo is sent with
        the text; additional attachments are currently skipped (a known
        simplification documented in the code map).

    Example:
        publisher = AiogramMessagePublisher(Bot(token))
        message_id = await publisher.publish_post(chat_id, post)
    """

    def __init__(self, bot: Bot) -> None:
        """
        Args:
            bot: An aiogram :class:`Bot` created with the main bot token.
                The bot must be an admin of every destination channel.
        """
        self._bot = bot

    async def publish_text(self, chat_id: int, text: str) -> int:
        """
        Send a plain text message.

        Args:
            chat_id: Destination channel chat id.
            text: UTF-8 message text (Persian preserved exactly).

        Returns:
            The Telegram message id.

        Raises:
            TelegramPublishError: When the Bot API call fails.
        """
        try:
            message = await self._bot.send_message(chat_id, text)
        except Exception as exc:
            raise TelegramPublishError(
                f"send_message failed chat={chat_id}: {exc}"
            ) from exc
        return message.message_id

    async def publish_post(self, chat_id: int, post: Post) -> int:
        """
        Publish a collected post, including its first media file if present.

        Args:
            chat_id: Destination channel chat id.
            post: The approved post.

        Returns:
            The Telegram message id of the (first) published message.

        Raises:
            TelegramPublishError: When the Bot API call fails or the
                stored media file no longer exists.
        """
        media = self._first_existing_media(post)
        text = post.text or ""
        try:
            if media is not None:
                media_kind, path = media
                input_file = FSInputFile(str(path))
                sender = self._media_sender(media_kind)
                if len(text) <= _CAPTION_LIMIT:
                    message = await sender(chat_id, input_file, caption=text or None)
                else:
                    message = await sender(chat_id, input_file)
                    await self._bot.send_message(chat_id, text)
                return message.message_id
            if not text:
                raise TelegramPublishError(f"Post {post.post_id} has no publishable content")
            message = await self._bot.send_message(chat_id, text)
            return message.message_id
        except TelegramPublishError:
            raise
        except Exception as exc:
            raise TelegramPublishError(
                f"publish_post failed post={post.post_id} chat={chat_id}: {exc}"
            ) from exc

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        """
        Delete a message previously published by the bot.

        Args:
            chat_id: Destination channel chat id.
            message_id: Telegram message id to delete.

        Raises:
            TelegramPublishError: When Telegram rejects deletion.
        """
        try:
            await self._bot.delete_message(chat_id, message_id)
        except Exception as exc:
            raise TelegramPublishError(
                f"delete_message failed chat={chat_id} message={message_id}: {exc}"
            ) from exc

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

    def _media_sender(self, kind: MediaKind) -> object:
        """Return the Bot API send method for a media kind."""
        if kind == MediaKind.PHOTO:
            return self._bot.send_photo
        if kind == MediaKind.VIDEO:
            return self._bot.send_video
        return self._bot.send_document
