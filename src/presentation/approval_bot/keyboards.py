"""Inline keyboard builders for the approval bot.

Callback data format (max 64 bytes):
    ``apv:pub:<post_id>:<chat_id>`` -> toggle immediate publish/delete
    ``apv:sch:<post_id>:<chat_id>`` -> toggle native Telegram schedule/delete
    ``apv:nop:<reason>``            -> inert button for blocked opposite mode
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.domain.entities import DestinationChannel

CB_PREFIX = "apv"

MODE_IMMEDIATE = "i"
MODE_SCHEDULED = "s"


def build_channel_keyboard(
    post_id: str,
    channels: list[DestinationChannel],
    published_chat_ids: set[int],
    scheduled_chat_ids: set[int] | None = None,
    immediate: bool = False,
) -> InlineKeyboardMarkup:
    """
    Build the approval keyboard with direct two-button actions per channel.

    Args:
        post_id: Internal post id embedded in callback data.
        channels: All enabled destination channels.
        published_chat_ids: Channels where this post is currently published.
        scheduled_chat_ids: Channels where this post is currently scheduled.
        immediate: Ignored legacy argument kept for older call sites.

    Returns:
        Inline keyboard markup. Each channel row contains an immediate
        publish toggle and a native schedule toggle.
    """
    del immediate
    scheduled_chat_ids = scheduled_chat_ids or set()
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        published = channel.chat_id in published_chat_ids
        scheduled = channel.chat_id in scheduled_chat_ids
        rows.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"✅ فوری {channel.title}"
                        if published
                        else f"🚀 فوری {channel.title}"
                    ),
                    callback_data=(
                        f"{CB_PREFIX}:pub:{post_id}:{channel.chat_id}"
                        if not scheduled
                        else f"{CB_PREFIX}:nop:scheduled"
                    ),
                ),
                InlineKeyboardButton(
                    text=(
                        f"✅ اسکجول {channel.title}"
                        if scheduled
                        else f"⏱ اسکجول {channel.title}"
                    ),
                    callback_data=(
                        f"{CB_PREFIX}:sch:{post_id}:{channel.chat_id}"
                        if not published
                        else f"{CB_PREFIX}:nop:published"
                    ),
                ),
            ]
        )
    return InlineKeyboardMarkup(inline_keyboard=rows)
