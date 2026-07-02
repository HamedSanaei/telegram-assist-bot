"""Callback query handlers for the approval bot.

Every callback validates admin identity, post existence, channel
permission, and duplicate publishing state through the
:class:`ApprovalService` before acting.

Delivery modes: each approval message carries a toggle between the
default *scheduled* mode (the post enters the destination channel's
paced queue) and *immediate* mode (the post is published right away).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from src.application.approval_service import ApprovalService
from src.presentation.approval_bot.keyboards import (
    CB_PREFIX,
    MODE_IMMEDIATE,
    build_channel_keyboard,
    build_confirm_keyboard,
)
from src.shared.errors import ApprovalStateError, TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def _is_immediate(mode: str) -> bool:
    """Map a callback mode character to the immediate flag."""
    return mode == MODE_IMMEDIATE


def format_scheduled_time(scheduled_at: datetime, timezone_name: str) -> str:
    """
    Format a UTC schedule time for the admin answer in the local timezone.

    Args:
        scheduled_at: The UTC publish time.
        timezone_name: IANA timezone name such as ``Asia/Tehran``.

    Returns:
        ``HH:MM`` for same-day slots, ``YYYY-MM-DD HH:MM`` otherwise.

    Example:
        format_scheduled_time(datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc), "Asia/Tehran")
    """
    local_zone = ZoneInfo(timezone_name)
    local_time = scheduled_at.astimezone(local_zone)
    if local_time.date() == datetime.now(local_zone).date():
        return local_time.strftime("%H:%M")
    return local_time.strftime("%Y-%m-%d %H:%M")


def create_approval_router(
    approval: ApprovalService, timezone_name: str = "Asia/Tehran"
) -> Router:
    """
    Create the aiogram router handling approval callbacks.

    Args:
        approval: The approval service that performs all validation,
            publishing, and schedule queueing.
        timezone_name: Timezone used to display scheduled publish times.

    Returns:
        A configured :class:`Router` to include in the dispatcher.

    Example:
        dispatcher.include_router(create_approval_router(service))
    """
    router = Router(name="approval")

    @router.message(CommandStart())
    async def on_start(message: Message) -> None:
        """Confirm the bot is alive and whether this user is an admin."""
        user_id = message.from_user.id if message.from_user else 0
        try:
            await approval.ensure_admin(user_id)
        except ApprovalStateError:
            logger.warning("Non-admin user started the approval bot user=%s", user_id)
            await message.answer(
                "🤖 ربات تایید فعال است.\n"
                f"⛔️ شناسه شما ({user_id}) در فهرست ادمین‌ها نیست؛ "
                "برای دریافت پست‌ها باید این شناسه در "
                "telegram.admin_user_ids تنظیمات ثبت شود."
            )
            return
        logger.info("Admin started the approval bot user=%s", user_id)
        await message.answer(
            "🤖 ربات تایید فعال است.\n"
            "✅ شناسه شما به عنوان ادمین شناخته شد؛ "
            "پست‌های جدید برای تایید به همین گفتگو ارسال می‌شوند."
        )

    async def _refresh_channel_keyboard(
        callback: CallbackQuery, post_id: str, immediate: bool = False
    ) -> None:
        """Re-render the channel keyboard from current publish/queue state."""
        channels = await approval.list_channels()
        published = await approval.published_channels(post_id)
        scheduled = await approval.scheduled_channels(post_id)
        await callback.message.edit_reply_markup(
            reply_markup=build_channel_keyboard(
                post_id, channels, published, scheduled, immediate=immediate
            )
        )

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:nop"))
    async def on_noop(callback: CallbackQuery) -> None:
        """Answer inert (already published/scheduled) buttons quietly."""
        if callback.data.endswith(":sch"):
            await callback.answer("این پست در صف زمان‌بندی این کانال است.")
        else:
            await callback.answer("قبلا به این کانال ارسال شده است.")

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:mode:"))
    async def on_mode_toggle(callback: CallbackQuery) -> None:
        """Flip the delivery mode between scheduled and immediate."""
        _, _, post_id, mode = callback.data.split(":")
        immediate = _is_immediate(mode)
        try:
            await approval.ensure_admin(callback.from_user.id)
        except ApprovalStateError:
            await callback.answer("دسترسی غیرمجاز.", show_alert=True)
            return
        await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
        await callback.answer(
            "🚀 حالت ارسال فوری فعال شد." if immediate else "⏱ حالت زمان‌بندی فعال شد."
        )

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:send:"))
    async def on_channel_selected(callback: CallbackQuery) -> None:
        """Ask for final confirmation after a channel button is pressed."""
        parts = callback.data.split(":")
        post_id, chat_id_raw = parts[2], parts[3]
        immediate = _is_immediate(parts[4]) if len(parts) > 4 else False
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
                await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
                return
            if chat_id in await approval.scheduled_channels(post_id):
                await callback.answer("این پست در صف زمان‌بندی این کانال است.")
                await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
                return
        except ApprovalStateError as exc:
            logger.warning("Approval callback rejected: %s", exc)
            await callback.answer("این درخواست معتبر نیست (پست منقضی یا دسترسی غیرمجاز).", show_alert=True)
            return
        await callback.message.edit_reply_markup(
            reply_markup=build_confirm_keyboard(post_id, channel, immediate=immediate)
        )
        if immediate:
            await callback.answer(f"ارسال فوری به «{channel.title}»؟ تایید نهایی لازم است.")
        else:
            await callback.answer(f"زمان‌بندی برای «{channel.title}»؟ تایید نهایی لازم است.")

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:cfm:"))
    async def on_confirm(callback: CallbackQuery) -> None:
        """Publish or queue the post after final admin confirmation."""
        parts = callback.data.split(":")
        post_id, chat_id_raw = parts[2], parts[3]
        immediate = _is_immediate(parts[4]) if len(parts) > 4 else True
        chat_id = int(chat_id_raw)
        try:
            if immediate:
                await approval.publish(post_id, chat_id, callback.from_user.id)
            else:
                scheduled_at = await approval.schedule_publish(
                    post_id, chat_id, callback.from_user.id
                )
        except ApprovalStateError as exc:
            logger.warning("Publish rejected post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("ارسال ممکن نیست (تکراری، منقضی یا غیرمجاز).", show_alert=True)
            await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
            return
        except TelegramPublishError as exc:
            logger.error("Publish failed post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("خطا در ارسال به کانال. دوباره تلاش کنید.", show_alert=True)
            await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
            return
        await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
        if immediate:
            await callback.answer("✅ با موفقیت ارسال شد.")
        else:
            await callback.answer(
                f"⏱ زمان‌بندی شد برای {format_scheduled_time(scheduled_at, timezone_name)}"
            )

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:cxl:"))
    async def on_cancel(callback: CallbackQuery) -> None:
        """Restore the channel keyboard when confirmation is cancelled."""
        parts = callback.data.split(":")
        post_id = parts[2]
        immediate = _is_immediate(parts[3]) if len(parts) > 3 else False
        try:
            await approval.ensure_admin(callback.from_user.id)
        except ApprovalStateError:
            await callback.answer("دسترسی غیرمجاز.", show_alert=True)
            return
        await _refresh_channel_keyboard(callback, post_id, immediate=immediate)
        await callback.answer("لغو شد.")

    return router
