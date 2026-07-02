"""Approval request sender (implements :class:`ApprovalNotifier`)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramRetryAfter
from aiogram.types import FSInputFile

from src.domain.entities import DestinationChannel, Post
from src.domain.enums import MediaKind, PostCategory
from src.domain.interfaces import AdminRepository, ChannelRepository
from src.presentation.approval_bot.keyboards import build_channel_keyboard
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_PREVIEW_TEXT_LIMIT = 3000
_CAPTION_LIMIT = 1024
_ADMIN_SEND_DELAY_SECONDS = 0.25

_CATEGORY_LABELS: dict[PostCategory, str] = {
    PostCategory.GENERAL_NEWS: "خبر عمومی",
    PostCategory.BREAKING_NEWS: "خبر فوری",
    PostCategory.TECHNOLOGY: "تکنولوژی",
    PostCategory.VPN: "وی‌پی‌ان",
    PostCategory.VPN_CONFIG: "کانفیگ وی‌پی‌ان",
    PostCategory.IRRELEVANT: "نامرتبط",
}


def build_preview_text(post: Post, source_label: str | None = None) -> str:
    """
    Build the Persian preview message shown to admins for approval.

    Args:
        post: The post awaiting approval.
        source_label: Optional readable source channel label.

    Returns:
        The formatted UTF-8 preview text.
    """
    category = _CATEGORY_LABELS.get(post.category, "نامشخص") if post.category else "نامشخص"
    lines = [
        "🆕 پست جدید در انتظار تایید",
        f"🏷 دسته‌بندی: {category}",
        f"📡 منبع: {source_label or post.source_chat_id}",
    ]
    if post.media:
        lines.append(f"📎 دارای {len(post.media)} پیوست رسانه‌ای")
    if post.vpn_configs:
        working = sum(1 for c in post.vpn_configs if c.test_status.value == "working")
        lines.append(f"🔐 کانفیگ‌ها: {len(post.vpn_configs)} (سالم از ایران: {working})")
    lines.append("")
    lines.append(post.text[:_PREVIEW_TEXT_LIMIT] if post.text else "(بدون متن)")
    return "\n".join(lines)


def _first_existing_media(post: Post) -> tuple[MediaKind, Path] | None:
    """
    Return the first downloaded media path for an approval preview.

    Args:
        post: The post awaiting approval.

    Returns:
        The first existing local media kind and path, or ``None`` when the
        post has no downloaded media file.
    """
    preferred_order = (MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.DOCUMENT)
    by_kind = {kind: [] for kind in preferred_order}
    for media in post.media:
        if media.kind in by_kind and media.file_path:
            by_kind[media.kind].append(Path(media.file_path))
    for kind in preferred_order:
        for path in by_kind[kind]:
            if path.exists():
                return kind, path
    return None


class AiogramApprovalNotifier:
    """
    Sends approval request messages with channel buttons to every
    configured admin via the approval bot.

    Example:
        notifier = AiogramApprovalNotifier(approval_bot, admin_repo)
        await notifier.send_approval_request(post, channels)
    """

    def __init__(
        self,
        bot: Bot,
        admins: AdminRepository,
        channels: ChannelRepository | None = None,
    ) -> None:
        """
        Args:
            bot: aiogram bot created with the approval bot token.
            admins: Admin repository providing recipient user ids.
            channels: Optional channel repository used to display source
                channel names instead of raw numeric chat ids.
        """
        self._bot = bot
        self._admins = admins
        self._channels = channels

    async def send_approval_request(
        self, post: Post, channels: list[DestinationChannel]
    ) -> None:
        """
        Send the approval message to all admins.

        Args:
            post: The post awaiting approval.
            channels: Enabled destination channels for the buttons.

        Side effects:
            One Telegram message per admin. Per-admin failures are
            logged and do not block delivery to the other admins.
        """
        text = build_preview_text(post, await self._source_label(post))
        keyboard = build_channel_keyboard(post.post_id, channels, published_chat_ids=set())
        for admin_id in await self._admins.list_user_ids():
            try:
                await self._send_preview(admin_id, post, text, keyboard)
                await asyncio.sleep(_ADMIN_SEND_DELAY_SECONDS)
            except Exception as exc:
                logger.error(
                    "Approval message failed admin=%s post=%s error=%s "
                    "(hint: the admin must open the approval bot and press "
                    "Start once before the bot can message them)",
                    admin_id,
                    post.post_id,
                    exc,
                )

    async def _send_preview(
        self, admin_id: int, post: Post, text: str, keyboard: object
    ) -> None:
        """
        Send the approval preview, including the first media file when present.

        Args:
            admin_id: Telegram user id of the admin recipient.
            post: The post awaiting approval.
            text: Preview text built by :func:`build_preview_text`.
            keyboard: Inline keyboard with destination channel buttons.

        Side effects:
            Sends one or two Telegram messages. The inline keyboard is
            attached to the message that contains the preview text.
        """
        media = _first_existing_media(post)
        if media is None:
            await self._send_with_retry(
                self._bot.send_message, admin_id, text, reply_markup=keyboard
            )
            return

        media_kind, path = media
        input_file = FSInputFile(str(path))
        sender = self._media_sender(media_kind)
        if len(text) <= _CAPTION_LIMIT:
            await self._send_with_retry(
                sender,
                admin_id,
                input_file,
                caption=text,
                reply_markup=keyboard,
            )
            return

        await self._send_with_retry(sender, admin_id, input_file)
        await self._send_with_retry(
            self._bot.send_message, admin_id, text, reply_markup=keyboard
        )

    def _media_sender(self, kind: MediaKind) -> object:
        """Return the Bot API send method for the media kind."""
        if kind == MediaKind.PHOTO:
            return self._bot.send_photo
        if kind == MediaKind.VIDEO:
            return self._bot.send_video
        return self._bot.send_document

    async def _source_label(self, post: Post) -> str | None:
        """Return a readable source channel label for the preview."""
        if self._channels is None:
            return None
        return await self._channels.get_source_label(post.source_chat_id)

    async def _send_with_retry(self, sender: object, *args: object, **kwargs: object) -> object:
        """
        Execute one Bot API send and obey Telegram flood-wait responses.

        Args:
            sender: Bound aiogram send method.
            *args: Positional arguments for the send method.
            **kwargs: Keyword arguments for the send method.

        Returns:
            The aiogram message returned by the sender.

        Raises:
            Exception: Re-raises the final send error after one retry.
        """
        try:
            return await sender(*args, **kwargs)
        except TelegramRetryAfter as exc:
            delay = int(getattr(exc, "retry_after", 1)) + 1
            logger.warning("Telegram rate limit hit; retrying after %ss", delay)
            await asyncio.sleep(delay)
            return await sender(*args, **kwargs)
