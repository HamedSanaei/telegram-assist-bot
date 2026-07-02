"""Inline keyboard builders for the approval bot.

Callback data format (max 64 bytes), where ``<m>`` is the delivery mode
(``s`` = scheduled via the channel queue, ``i`` = immediate):
    ``apv:mode:<post_id>:<m>``            -> admin toggled the delivery mode
    ``apv:send:<post_id>:<chat_id>:<m>``  -> admin picked a channel
    ``apv:cfm:<post_id>:<chat_id>:<m>``   -> admin confirmed publishing
    ``apv:cxl:<post_id>:<m>``             -> admin cancelled confirmation
    ``apv:nop:pub``                       -> inert button (already published)
    ``apv:nop:sch``                       -> inert button (already scheduled)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.domain.entities import DestinationChannel

CB_PREFIX = "apv"

MODE_IMMEDIATE = "i"
MODE_SCHEDULED = "s"


def _mode_toggle_row(post_id: str, immediate: bool) -> list[InlineKeyboardButton]:
    """
    Build the delivery-mode toggle row.

    The button shows the current mode; pressing it switches to the other
    mode. Scheduled is the default policy.
    """
    if immediate:
        text = "🚀 حالت ارسال: فوری (برای زمان‌بندی بزنید)"
        target = MODE_SCHEDULED
    else:
        text = "⏱ حالت ارسال: زمان‌بندی‌شده (برای فوری بزنید)"
        target = MODE_IMMEDIATE
    return [
        InlineKeyboardButton(
            text=text,
            callback_data=f"{CB_PREFIX}:mode:{post_id}:{target}",
        )
    ]


def build_channel_keyboard(
    post_id: str,
    channels: list[DestinationChannel],
    published_chat_ids: set[int],
    scheduled_chat_ids: set[int] | None = None,
    immediate: bool = False,
) -> InlineKeyboardMarkup:
    """
    Build the main approval keyboard with one button per channel.

    The first row toggles the delivery mode (scheduled queue by default,
    immediate on demand). Channels the post was already published to are
    rendered with ✅, channels with a pending scheduled publish with ⏱;
    both are inert so the post cannot be queued or sent twice.

    Args:
        post_id: Internal post id embedded in callback data.
        channels: All enabled destination channels.
        published_chat_ids: Channels already published to.
        scheduled_chat_ids: Channels with a pending scheduled publish.
        immediate: Currently selected delivery mode.

    Returns:
        The inline keyboard markup.
    """
    scheduled_chat_ids = scheduled_chat_ids or set()
    mode = MODE_IMMEDIATE if immediate else MODE_SCHEDULED
    rows: list[list[InlineKeyboardButton]] = [_mode_toggle_row(post_id, immediate)]
    for channel in channels:
        if channel.chat_id in published_chat_ids:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ {channel.title}",
                        callback_data=f"{CB_PREFIX}:nop:pub",
                    )
                ]
            )
        elif channel.chat_id in scheduled_chat_ids:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"⏱ {channel.title} (در صف)",
                        callback_data=f"{CB_PREFIX}:nop:sch",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"ارسال به {channel.title}",
                        callback_data=f"{CB_PREFIX}:send:{post_id}:{channel.chat_id}:{mode}",
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_confirm_keyboard(
    post_id: str, channel: DestinationChannel, immediate: bool = False
) -> InlineKeyboardMarkup:
    """
    Build the final-confirmation keyboard for one selected channel.

    Args:
        post_id: Internal post id.
        channel: The channel the admin selected.
        immediate: Delivery mode carried over from the channel keyboard.

    Returns:
        The inline keyboard with confirm and cancel buttons.
    """
    mode = MODE_IMMEDIATE if immediate else MODE_SCHEDULED
    if immediate:
        confirm_text = f"🚀 تایید ارسال فوری به {channel.title}"
    else:
        confirm_text = (
            f"⏱ تایید زمان‌بندی برای {channel.title} "
            f"(هر {channel.post_interval_minutes} دقیقه)"
        )
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=confirm_text,
                    callback_data=f"{CB_PREFIX}:cfm:{post_id}:{channel.chat_id}:{mode}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ انصراف",
                    callback_data=f"{CB_PREFIX}:cxl:{post_id}:{mode}",
                )
            ],
        ]
    )
