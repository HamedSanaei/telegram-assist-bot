"""Callback query handlers for the approval bot.

Every callback validates admin identity, post existence, channel permission,
and current publish state through :class:`ApprovalService` before acting.
Approval actions are direct toggles: click once to publish/schedule, click
again on the active button to delete/unschedule.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Bot, F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message

from src.application.approval_service import ApprovalService, ApprovalToggleResult
from src.presentation.approval_bot.keyboards import CB_PREFIX, build_channel_keyboard
from src.shared.errors import ApprovalStateError, TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def format_scheduled_time(scheduled_at: datetime, timezone_name: str) -> str:
    """
    Format a UTC schedule time for the admin answer in the local timezone.

    Args:
        scheduled_at: The UTC publish time.
        timezone_name: IANA timezone name such as ``Asia/Tehran``.

    Returns:
        ``HH:MM`` for same-day slots, ``YYYY-MM-DD HH:MM`` otherwise.
    """
    local_zone = ZoneInfo(timezone_name)
    local_time = scheduled_at.astimezone(local_zone)
    if local_time.date() == datetime.now(local_zone).date():
        return local_time.strftime("%H:%M")
    return local_time.strftime("%Y-%m-%d %H:%M")


def _answer_for_result(
    result: ApprovalToggleResult, timezone_name: str
) -> str:
    """Return a short Persian callback answer for a toggle result."""
    if result.action == "published":
        return "✅ فوری ارسال شد."
    if result.action == "unpublished":
        return "🗑 پست فوری از کانال حذف شد."
    if result.action == "scheduled":
        when = (
            format_scheduled_time(result.scheduled_at, timezone_name)
            if result.scheduled_at is not None
            else "زمان‌بندی تلگرام"
        )
        return f"⏱ اسکجول شد برای {when}"
    if result.action == "unscheduled":
        return "🗑 از اسکجول کانال حذف شد."
    return "انجام شد."


def create_approval_router(
    approval: ApprovalService, timezone_name: str = "Asia/Tehran"
) -> Router:
    """
    Create the aiogram router handling approval callbacks.

    Args:
        approval: The approval service that performs validation,
            publishing, scheduling, deletion, and unscheduling.
        timezone_name: Timezone used to display scheduled publish times.

    Returns:
        A configured :class:`Router` to include in the dispatcher.
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

    async def _refresh_clicked_keyboard(callback: CallbackQuery, post_id: str) -> None:
        """Re-render the clicked message keyboard from current state."""
        if callback.message is None:
            return
        channels = await approval.list_channels()
        published = await approval.published_channels(post_id)
        scheduled = await approval.scheduled_channels(post_id)
        await callback.message.edit_reply_markup(
            reply_markup=build_channel_keyboard(
                post_id, channels, published, scheduled
            )
        )

    async def _refresh_all_approval_keyboards(
        callback: CallbackQuery, post_id: str
    ) -> None:
        """Best-effort refresh of every admin approval message for a post."""
        refs = await approval.active_approval_messages(post_id)
        if not refs:
            await _refresh_clicked_keyboard(callback, post_id)
            return
        channels = await approval.list_channels()
        published = await approval.published_channels(post_id)
        scheduled = await approval.scheduled_channels(post_id)
        bot: Bot = callback.bot
        for ref in refs:
            try:
                await bot.edit_message_reply_markup(
                    chat_id=ref.chat_id,
                    message_id=ref.message_id,
                    reply_markup=build_channel_keyboard(
                        post_id, channels, published, scheduled
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "Approval keyboard refresh failed post=%s chat=%s message=%s "
                    "ref=%s error=%s",
                    post_id,
                    ref.chat_id,
                    ref.message_id,
                    ref.id,
                    exc,
                )
                if ref.id is not None:
                    await approval.deactivate_approval_message(ref.id)

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:nop"))
    async def on_noop(callback: CallbackQuery) -> None:
        """Answer buttons blocked by the opposite active mode."""
        if callback.data.endswith(":scheduled"):
            await callback.answer("اول اسکجول همین کانال را حذف کنید.")
        elif callback.data.endswith(":published"):
            await callback.answer("اول ارسال فوری همین کانال را حذف کنید.")
        else:
            await callback.answer("این دکمه فعلا فعال نیست.")

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:pub:"))
    async def on_publish_toggle(callback: CallbackQuery) -> None:
        """Toggle immediate publish/delete without a confirmation step."""
        parts = callback.data.split(":")
        post_id, chat_id = parts[2], int(parts[3])
        try:
            result = await approval.toggle_publish(
                post_id, chat_id, callback.from_user.id
            )
        except ApprovalStateError as exc:
            logger.warning("Immediate toggle rejected post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("ارسال فوری ممکن نیست یا حالت دیگری فعال است.", show_alert=True)
            await _refresh_all_approval_keyboards(callback, post_id)
            return
        except TelegramPublishError as exc:
            logger.error("Immediate toggle failed post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("خطا در ارسال/حذف فوری. دوباره تلاش کنید.", show_alert=True)
            await _refresh_all_approval_keyboards(callback, post_id)
            return
        await _refresh_all_approval_keyboards(callback, post_id)
        await callback.answer(_answer_for_result(result, timezone_name))

    @router.callback_query(F.data.startswith(f"{CB_PREFIX}:sch:"))
    async def on_schedule_toggle(callback: CallbackQuery) -> None:
        """Toggle native Telegram schedule/delete without confirmation."""
        parts = callback.data.split(":")
        post_id, chat_id = parts[2], int(parts[3])
        try:
            result = await approval.toggle_schedule(
                post_id, chat_id, callback.from_user.id
            )
        except ApprovalStateError as exc:
            logger.warning("Schedule toggle rejected post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("اسکجول ممکن نیست یا حالت دیگری فعال است.", show_alert=True)
            await _refresh_all_approval_keyboards(callback, post_id)
            return
        except TelegramPublishError as exc:
            logger.error("Schedule toggle failed post=%s chat=%s: %s", post_id, chat_id, exc)
            await callback.answer("خطا در اسکجول/حذف اسکجول. دوباره تلاش کنید.", show_alert=True)
            await _refresh_all_approval_keyboards(callback, post_id)
            return
        await _refresh_all_approval_keyboards(callback, post_id)
        await callback.answer(_answer_for_result(result, timezone_name))

    return router
