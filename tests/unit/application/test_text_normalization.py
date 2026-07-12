"""Verify the minimal exact-normalization table."""

from telegram_assist_bot.application.text_normalization import (
    exact_content_hash,
    normalize_exact_text,
)


def test_only_line_endings_and_trailing_space_change() -> None:
    assert normalize_exact_text("سلام‌\r\nي ك 😀  \t") == "سلام‌\nي ك 😀"
    assert normalize_exact_text("ی") != normalize_exact_text("ي")
    assert normalize_exact_text("ک") != normalize_exact_text("ك")
    assert normalize_exact_text("می‌رود") == "می‌رود"


def test_hash_is_deterministic_and_media_order_matters() -> None:
    first = exact_content_hash(text="سلام", caption=None, media_hashes=("a", "b"))
    assert first == exact_content_hash(
        text="سلام", caption=None, media_hashes=("a", "b")
    )
    assert first != exact_content_hash(
        text="سلام", caption=None, media_hashes=("b", "a")
    )
