"""Inline keyboard builders for the approval bot.

Callback data format (max 64 bytes):
    ``apv:send:<post_id>:<chat_id>``  -> admin picked a channel
    ``apv:cfm:<post_id>:<chat_id>``   -> admin confirmed publishing
    ``apv:cxl:<post_id>``             -> admin cancelled confirmation
    ``apv:nop``                       -> inert button (already published)
"""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from src.domain.entities import DestinationChannel

CB_PREFIX = "apv"


def build_channel_keyboard(
    post_id: str,
    channels: list[DestinationChannel],
    published_chat_ids: set[int],
) -> InlineKeyboardMarkup:
    """
    Build the main approval keyboard with one button per channel.

    Channels the post was already published to are rendered with a ✅
    mark and an inert callback so they cannot be published twice.

    Args:
        post_id: Internal post id embedded in callback data.
        channels: All enabled destination channels.
        published_chat_ids: Channels already published to.

    Returns:
        The inline keyboard markup.
    """
    rows: list[list[InlineKeyboardButton]] = []
    for channel in channels:
        if channel.chat_id in published_chat_ids:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"✅ {channel.title}",
                        callback_data=f"{CB_PREFIX}:nop",
                    )
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"ارسال به {channel.title}",
                        callback_data=f"{CB_PREFIX}:send:{post_id}:{channel.chat_id}",
                    )
                ]
            )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_confirm_keyboard(
    post_id: str, channel: DestinationChannel
) -> InlineKeyboardMarkup:
    """
    Build the final-confirmation keyboard for one selected channel.

    Args:
        post_id: Internal post id.
        channel: The channel the admin selected.

    Returns:
        The inline keyboard with confirm and cancel buttons.
    """
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ تایید ارسال به {channel.title}",
                    callback_data=f"{CB_PREFIX}:cfm:{post_id}:{channel.chat_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    text="↩️ انصراف",
                    callback_data=f"{CB_PREFIX}:cxl:{post_id}",
                )
            ],
        ]
    )
