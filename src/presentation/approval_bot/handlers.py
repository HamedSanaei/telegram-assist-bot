"""Callback query handlers for the approval bot.

Every callback validates admin identity, post existence, channel
permission, and duplicate publishing state through the
:class:`ApprovalService` before acting.
"""

from __future__ import annotations

from aiogram import F, Router
from aiogram.types import CallbackQuery

from src.application.approval_service import ApprovalService
from src.presentation.approval_bot.keyboards import (
    CB_PREFIX,
    build_channel_keyboard,
    build_confirm_keyboard,
)
from src.shared.errors import ApprovalStateError, TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def create_approval_router(approval: ApprovalService) -> Router:
    """
    Create the aiogram router handling approval callbacks.

    Args:
        approval: The approval service that performs all validation
            and publishing.

    Returns:
        A configured :class:`Router` to include in the dispatcher.

    Example:
        dispatcher.include_router(create_approval_router(service))
    """
    router = Router(name="approval")

    async def _refresh_channel_keyboard(callback: CallbackQuery, post_id: str) -> None:
        """Re-render the channel keyboard from current publish state."""
        channels = await approval.list_channels()
        published = await approval.published_channels(post_id)
        await callback.message.edit_reply_markup(
            reply_markup=build_channel_keyboard(post_id, channels, published)
        )

    @router.callback_query(F.data == f"{CB_PREFIX}:nop")
    async def on_noop(callback: CallbackQuery) -> None:
        """Answer inert (already published) buttons quietly."""
        await callback.answer("قبلا به این کانال ارسال شده است.")

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:send:"))
    async def on_channel_selected(callback: CallbackQuery) -> None:
        """Ask for final confirmation after a channel button is pressed."""
        _, _, post_id, chat_id_raw = callback.data.split(":")
        chat_id = int(chat_id_raw)
        try:
            await approval.ensure_admin(callback.from_user.id)
            await approval.get_post(post_id)
            channels = await approval.list_channels()
            channel = next((c for c in channels if c.chat_id == chat_id), None)
            if channel is None:
                raise ApprovalStateError(f"Unknown channel {chat_id}")
            if chat_id in await approval.published_channels(post_id):
                await callback.answer("قبلا به این کانال ارسال شده است.")
                await _refresh_channel_keyboard(callback, post_id)
                return
        except ApprovalStateError as exc:
            logger.warning("Approval callback rejected: %s", exc)
            await callback.answer("این درخواست معتبر نیست (پست منقضی یا دسترسی غیرمجاز).", show_alert=True)
            return
        await callback.message.edit_reply_markup(
            reply_markup=build_confirm_keyboard(post_id, channel)
        )
        await callback.answer(f"ارسال به «{channel.title}»؟ تایید نهایی لازم است.")

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:cfm:"))
    async def on_confirm(callback: CallbackQuery) -> None:
        """Publish the post after final admin confirmation."""
        _, _, post_id, chat_id_raw = callback.data.split(":")
        chat_id = int(chat_id_raw)
        try:
            await approval.publish(post_id, chat_id, callback.from_user.id)
        except ApprovalStateError as exc:
            logger.warning("Publish rejected post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("ارسال ممکن نیست (تکراری، منقضی یا غیرمجاز).", show_alert=True)
            await _refresh_channel_keyboard(callback, post_id)
            return
        except TelegramPublishError as exc:
            logger.error("Publish failed post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("خطا در ارسال به کانال. دوباره تلاش کنید.", show_alert=True)
            await _refresh_channel_keyboard(callback, post_id)
            return
        await _refresh_channel_keyboard(callback, post_id)
        await callback.answer("✅ با موفقیت ارسال شد.")

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:cxl:"))
    async def on_cancel(callback: CallbackQuery) -> None:
        """Restore the channel keyboard when confirmation is cancelled."""
        _, _, post_id = callback.data.split(":")
        try:
            await approval.ensure_admin(callback.from_user.id)
        except ApprovalStateError:
            await callback.answer("دسترسی غیرمجاز.", show_alert=True)
            return
        await _refresh_channel_keyboard(callback, post_id)
        await callback.answer("لغو شد.")

    return router
