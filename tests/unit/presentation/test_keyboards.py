"""Unit tests for the approval bot inline keyboards."""

from __future__ import annotations

from src.domain.entities import DestinationChannel
from src.presentation.approval_bot.keyboards import build_channel_keyboard

POST_ID = "a" * 32
NEWS = DestinationChannel(chat_id=-100200, title="News", post_interval_minutes=15)
VPN = DestinationChannel(chat_id=-100300, title="VPN")


def _flat_buttons(markup) -> list:
    """Return all keyboard buttons as a flat list."""
    return [button for row in markup.inline_keyboard for button in row]


class TestChannelKeyboard:
    """Tests for :func:`build_channel_keyboard`."""

    def test_each_channel_has_immediate_and_schedule_buttons(self) -> None:
        markup = build_channel_keyboard(POST_ID, [NEWS], published_chat_ids=set())
        row = markup.inline_keyboard[0]
        assert len(row) == 2
        assert row[0].callback_data == f"apv:pub:{POST_ID}:{NEWS.chat_id}"
        assert row[1].callback_data == f"apv:sch:{POST_ID}:{NEWS.chat_id}"
        assert "فوری" in row[0].text
        assert "اسکجول" in row[1].text

    def test_published_button_stays_clickable_for_delete(self) -> None:
        markup = build_channel_keyboard(
            POST_ID,
            [NEWS],
            published_chat_ids={NEWS.chat_id},
        )
        row = markup.inline_keyboard[0]
        assert row[0].text.startswith("✅ فوری")
        assert row[0].callback_data == f"apv:pub:{POST_ID}:{NEWS.chat_id}"
        assert row[1].callback_data == "apv:nop:published"

    def test_scheduled_button_stays_clickable_for_delete(self) -> None:
        markup = build_channel_keyboard(
            POST_ID,
            [NEWS],
            published_chat_ids=set(),
            scheduled_chat_ids={NEWS.chat_id},
        )
        row = markup.inline_keyboard[0]
        assert row[0].callback_data == "apv:nop:scheduled"
        assert row[1].text.startswith("✅ اسکجول")
        assert row[1].callback_data == f"apv:sch:{POST_ID}:{NEWS.chat_id}"

    def test_callback_data_fits_telegram_limit(self) -> None:
        markup = build_channel_keyboard(
            POST_ID,
            [DestinationChannel(chat_id=-1009999999999, title="طولانی")],
            published_chat_ids=set(),
        )
        for button in _flat_buttons(markup):
            assert len(button.callback_data.encode("utf-8")) <= 64

    def test_delivery_history_row_is_rendered_above_channels(self) -> None:
        """Posts with prior delivery state show one prominent history row."""
        markup = build_channel_keyboard(
            POST_ID,
            [NEWS],
            published_chat_ids={NEWS.chat_id},
            has_delivery_history=True,
        )

        assert len(markup.inline_keyboard[0]) == 1
        assert "قبلاً ارسال" in markup.inline_keyboard[0][0].text
        assert markup.inline_keyboard[0][0].callback_data == f"apv:history:{POST_ID}"
