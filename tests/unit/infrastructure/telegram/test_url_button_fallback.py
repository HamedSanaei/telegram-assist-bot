"""Verify bounded User API text fallback for portable source URL buttons."""

import pytest

from telegram_assist_bot.domain.posts import TelegramUrlButton
from telegram_assist_bot.infrastructure.telegram.url_button_fallback import (
    materialize_url_buttons,
)

_BUTTON = TelegramUrlButton("Connect", "https://example.invalid/proxy")
_KEYBOARD = ((_BUTTON,),)
_RENDERED = "Connect: https://example.invalid/proxy"


def test_preserves_text_and_row_order_without_normalizing_urls() -> None:
    second = TelegramUrlButton(
        "اتصال 🚀",
        "tg://proxy?server=نمونه.test&port=443&secret=abcdef",
    )

    result = materialize_url_buttons(
        "پروکسی‌ آماده ✨",
        (),
        ((_BUTTON, second), (TelegramUrlButton("راهنما", "https://example.com"),)),
        has_media=False,
    )

    assert result.text == (
        "پروکسی‌ آماده ✨\n\n"
        "Connect: https://example.invalid/proxy\n"
        "اتصال 🚀: tg://proxy?server=نمونه.test&port=443&secret=abcdef\n\n"
        "راهنما: https://example.com"
    )
    assert tuple(entity.entity_type for entity in result.entities) == (
        "url",
        "url",
        "url",
    )
    assert result.entities[0].offset_utf16 == (
        len("پروکسی‌ آماده ✨\n\nConnect: ".encode("utf-16-le")) // 2
    )
    assert result.entities[0].length_utf16 == len(_BUTTON.url)


def test_empty_keyboard_leaves_nullable_text_unchanged() -> None:
    assert materialize_url_buttons(None, (), (), has_media=False).text is None
    assert materialize_url_buttons("متن", (), (), has_media=True).text == "متن"


@pytest.mark.parametrize(
    ("has_media", "limit"),
    [(False, 4096), (True, 1024)],
)
def test_accepts_exact_utf16_boundary_and_rejects_overflow(
    has_media: bool, limit: int
) -> None:
    separator_and_button = f"\n\n{_RENDERED}"
    exact = "😀" * ((limit - len(separator_and_button)) // 2)
    if len(exact.encode("utf-16-le")) // 2 + len(separator_and_button) < limit:
        exact += "x"

    result = materialize_url_buttons(exact, (), _KEYBOARD, has_media=has_media)

    assert result.text is not None
    assert len(result.text.encode("utf-16-le")) // 2 == limit
    assert result.entities[-1].length_utf16 == len(_BUTTON.url)
    with pytest.raises(ValueError, match="exceeds Telegram"):
        materialize_url_buttons(f"{exact}x", (), _KEYBOARD, has_media=has_media)
