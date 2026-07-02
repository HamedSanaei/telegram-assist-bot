"""Approval request sender (implements :class:`ApprovalNotifier`)."""

from __future__ import annotations

from aiogram import Bot

from src.domain.entities import DestinationChannel, Post
from src.domain.enums import PostCategory
from src.domain.interfaces import AdminRepository
from src.presentation.approval_bot.keyboards import build_channel_keyboard
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

_PREVIEW_TEXT_LIMIT = 3000

_CATEGORY_LABELS: dict[PostCategory, str] = {
    PostCategory.GENERAL_NEWS: "خبر عمومی",
    PostCategory.BREAKING_NEWS: "خبر فوری",
    PostCategory.TECHNOLOGY: "تکنولوژی",
    PostCategory.VPN: "وی‌پی‌ان",
    PostCategory.VPN_CONFIG: "کانفیگ وی‌پی‌ان",
    PostCategory.IRRELEVANT: "نامرتبط",
}


def build_preview_text(post: Post) -> str:
    """
    Build the Persian preview message shown to admins for approval.

    Args:
        post: The post awaiting approval.

    Returns:
        The formatted UTF-8 preview text.
    """
    category = _CATEGORY_LABELS.get(post.category, "نامشخص") if post.category else "نامشخص"
    lines = [
        "🆕 پست جدید در انتظار تایید",
        f"🏷 دسته‌بندی: {category}",
        f"📡 منبع: {post.source_chat_id}",
    ]
    if post.media:
        lines.append(f"📎 دارای {len(post.media)} پیوست رسانه‌ای")
    if post.vpn_configs:
        working = sum(1 for c in post.vpn_configs if c.test_status.value == "working")
        lines.append(f"🔐 کانفیگ‌ها: {len(post.vpn_configs)} (سالم از ایران: {working})")
    lines.append("")
    lines.append(post.text[:_PREVIEW_TEXT_LIMIT] if post.text else "(بدون متن)")
    return "\n".join(lines)


class AiogramApprovalNotifier:
    """
    Sends approval request messages with channel buttons to every
    configured admin via the approval bot.

    Example:
        notifier = AiogramApprovalNotifier(approval_bot, admin_repo)
        await notifier.send_approval_request(post, channels)
    """

    def __init__(self, bot: Bot, admins: AdminRepository) -> None:
        """
        Args:
            bot: aiogram bot created with the approval bot token.
            admins: Admin repository providing recipient user ids.
        """
        self._bot = bot
        self._admins = admins

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
        text = build_preview_text(post)
        keyboard = build_channel_keyboard(post.post_id, channels, published_chat_ids=set())
        for admin_id in await self._admins.list_user_ids():
            try:
                await self._bot.send_message(admin_id, text, reply_markup=keyboard)
            except Exception as exc:
                logger.error(
                    "Approval message failed admin=%s post=%s error=%s "
                    "(hint: the admin must open the approval bot and press "
                    "Start once before the bot can message them)",
                    admin_id,
                    post.post_id,
                    exc,
                )
