"""Unit tests for the approval bot inline keyboards."""

from __future__ import annotations

from src.domain.entities import DestinationChannel
from src.presentation.approval_bot.keyboards import (
    MODE_IMMEDIATE,
    MODE_SCHEDULED,
    build_channel_keyboard,
    build_confirm_keyboard,
)

POST_ID = "a" * 32
NEWS = DestinationChannel(chat_id=-100200, title="News", post_interval_minutes=15)
VPN = DestinationChannel(chat_id=-100300, title="VPN")


def _flat_buttons(markup) -> list:
    """Return all keyboard buttons as a flat list."""
    return [button for row in markup.inline_keyboard for button in row]


class TestChannelKeyboard:
    """Tests for :func:`build_channel_keyboard`."""

    def test_default_mode_is_scheduled(self) -> None:
        markup = build_channel_keyboard(POST_ID, [NEWS], published_chat_ids=set())
        toggle = markup.inline_keyboard[0][0]
        assert toggle.callback_data == f"apv:mode:{POST_ID}:{MODE_IMMEDIATE}"
        assert "زمان‌بندی" in toggle.text
        send = markup.inline_keyboard[1][0]
        assert send.callback_data == f"apv:send:{POST_ID}:{NEWS.chat_id}:{MODE_SCHEDULED}"

    def test_immediate_mode_marks_buttons(self) -> None:
        markup = build_channel_keyboard(
            POST_ID, [NEWS], published_chat_ids=set(), immediate=True
        )
        toggle = markup.inline_keyboard[0][0]
        assert toggle.callback_data == f"apv:mode:{POST_ID}:{MODE_SCHEDULED}"
        assert "فوری" in toggle.text
        send = markup.inline_keyboard[1][0]
        assert send.callback_data == f"apv:send:{POST_ID}:{NEWS.chat_id}:{MODE_IMMEDIATE}"

    def test_published_and_scheduled_channels_are_inert(self) -> None:
        markup = build_channel_keyboard(
            POST_ID,
            [NEWS, VPN],
            published_chat_ids={NEWS.chat_id},
            scheduled_chat_ids={VPN.chat_id},
        )
        published_button = markup.inline_keyboard[1][0]
        scheduled_button = markup.inline_keyboard[2][0]
        assert published_button.text.startswith("✅")
        assert published_button.callback_data == "apv:nop:pub"
        assert scheduled_button.text.startswith("⏱")
        assert scheduled_button.callback_data == "apv:nop:sch"

    def test_callback_data_fits_telegram_limit(self) -> None:
        markup = build_channel_keyboard(
            POST_ID,
            [DestinationChannel(chat_id=-1009999999999, title="طولانی")],
            published_chat_ids=set(),
        )
        for button in _flat_buttons(markup):
            assert len(button.callback_data.encode("utf-8")) <= 64


class TestConfirmKeyboard:
    """Tests for :func:`build_confirm_keyboard`."""

    def test_scheduled_confirm_mentions_interval(self) -> None:
        markup = build_confirm_keyboard(POST_ID, NEWS, immediate=False)
        confirm = markup.inline_keyboard[0][0]
        assert "15" in confirm.text
        assert confirm.callback_data == (
            f"apv:cfm:{POST_ID}:{NEWS.chat_id}:{MODE_SCHEDULED}"
        )
        cancel = markup.inline_keyboard[1][0]
        assert cancel.callback_data == f"apv:cxl:{POST_ID}:{MODE_SCHEDULED}"

    def test_immediate_confirm(self) -> None:
        markup = build_confirm_keyboard(POST_ID, NEWS, immediate=True)
        confirm = markup.inline_keyboard[0][0]
        assert "فوری" in confirm.text
        assert confirm.callback_data == (
            f"apv:cfm:{POST_ID}:{NEWS.chat_id}:{MODE_IMMEDIATE}"
        )
