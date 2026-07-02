"""Unit tests for source-channel mention rewriting."""

from __future__ import annotations

from src.application.channel_mention_rewriter import rewrite_source_channel_mentions


class TestRewriteSourceChannelMentions:
    """Tests for publish-time source channel replacement."""

    def test_replaces_at_handle(self) -> None:
        text = "خبر از @source_channel منتشر شد"
        result = rewrite_source_channel_mentions(text, ["@source_channel"], "@dest")
        assert result == "خبر از @dest منتشر شد"

    def test_replaces_tme_links(self) -> None:
        text = "لینک: https://t.me/source_channel/123 و t.me/source_channel"
        result = rewrite_source_channel_mentions(
            text, ["https://t.me/source_channel"], "@dest"
        )
        assert result == "لینک: @dest و @dest"

    def test_leaves_other_mentions_unchanged(self) -> None:
        text = "سلام @other"
        result = rewrite_source_channel_mentions(text, ["@source"], "@dest")
        assert result == text

    def test_empty_destination_disables_rewrite(self) -> None:
        text = "سلام @source"
        result = rewrite_source_channel_mentions(text, ["@source"], "")
        assert result == text
