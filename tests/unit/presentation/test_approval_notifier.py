"""Unit tests for approval preview formatting."""

from __future__ import annotations

from src.domain.entities import Post
from src.domain.enums import PostCategory
from src.presentation.approval_bot.notifier import build_preview_text


class TestApprovalPreview:
    """Tests for admin-facing approval preview text."""

    def test_preview_uses_source_label_when_available(self) -> None:
        post = Post(
            post_id="p1",
            source_chat_id=-100123,
            source_message_id=10,
            text="متن خبر",
            content_hash="hash",
            category=PostCategory.GENERAL_NEWS,
        )

        text = build_preview_text(post, source_label="کانال منبع (@source)")

        assert "📡 منبع: کانال منبع (@source)" in text
        assert "-100123" not in text
