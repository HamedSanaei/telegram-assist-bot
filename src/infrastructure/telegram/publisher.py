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
        Publish a collected post, including its first photo if present.

        Args:
            chat_id: Destination channel chat id.
            post: The approved post.

        Returns:
            The Telegram message id of the (first) published message.

        Raises:
            TelegramPublishError: When the Bot API call fails or the
                stored media file no longer exists.
        """
        photos = [
            m
            for m in post.media
            if m.kind == MediaKind.PHOTO and m.file_path and Path(m.file_path).exists()
        ]
        text = post.text or ""
        try:
            if photos:
                photo = FSInputFile(photos[0].file_path)
                if len(text) <= _CAPTION_LIMIT:
                    message = await self._bot.send_photo(chat_id, photo, caption=text or None)
                else:
                    message = await self._bot.send_photo(chat_id, photo)
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
