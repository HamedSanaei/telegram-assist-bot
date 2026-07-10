"""Best-effort approval keyboard propagation helpers."""

from __future__ import annotations

from aiogram import Bot

from src.application.approval_service import ApprovalService
from src.presentation.approval_bot.keyboards import MODE_IMMEDIATE, build_channel_keyboard
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


def _is_message_not_modified_error(exc: Exception) -> bool:
    """
    Return whether Telegram rejected an edit because nothing changed.

    Args:
        exc: Exception raised by aiogram while editing reply markup.

    Returns:
        ``True`` for Telegram's harmless "message is not modified" response.
    """
    return "message is not modified" in str(exc).lower()


async def refresh_approval_keyboards(
    bot: Bot, approval: ApprovalService, post_id: str
) -> int:
    """
    Refresh every active approval message for one post.

    Args:
        bot: Approval bot used to edit messages.
        approval: Approval service exposing current publish/schedule state.
        post_id: Internal post id.

    Returns:
        Number of messages successfully edited.

    Side effects:
        Failed/stale message references are marked inactive and skipped by
        later propagation attempts.
    """
    refs = await approval.active_approval_messages(post_id)
    if not refs:
        return 0
    channels = await approval.list_channels()
    published = await approval.published_channels(post_id)
    scheduled = await approval.scheduled_channels(post_id)
    history = await approval.delivery_history(post_id)
    refreshed = 0
    for ref in refs:
        immediate = ref.delivery_mode == MODE_IMMEDIATE
        try:
            await bot.edit_message_reply_markup(
                chat_id=ref.chat_id,
                message_id=ref.message_id,
                reply_markup=build_channel_keyboard(
                    post_id,
                    channels,
                    published,
                    scheduled,
                    immediate=immediate,
                    has_delivery_history=bool(history),
                ),
            )
            refreshed += 1
        except Exception as exc:
            if _is_message_not_modified_error(exc):
                refreshed += 1
                logger.debug(
                    "Approval keyboard already current post=%s chat=%s message=%s ref=%s",
                    post_id,
                    ref.chat_id,
                    ref.message_id,
                    ref.id,
                )
                continue
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
    return refreshed


async def refresh_all_approval_keyboards(bot: Bot, approval: ApprovalService) -> int:
    """
    Refresh every active approval message known to SQLite.

    Args:
        bot: Approval bot used to edit messages.
        approval: Approval service exposing tracked approval post ids.

    Returns:
        Total number of messages successfully edited.
    """
    total = 0
    for post_id in await approval.active_approval_post_ids():
        total += await refresh_approval_keyboards(bot, approval, post_id)
    if total:
        logger.info("Refreshed approval keyboards count=%d", total)
    return total
