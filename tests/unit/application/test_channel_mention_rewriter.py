"""Unit tests for source-channel mention rewriting."""

from __future__ import annotations

from src.application.channel_mention_rewriter import (
    rewrite_source_channel_mentions,
    rewrite_source_channel_mentions_with_entities,
)
from src.domain.entities import TextEntity


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

    def test_removes_other_mentions(self) -> None:
        text = "سلام @other"
        result = rewrite_source_channel_mentions(text, ["@source"], "@dest")
        assert result == "سلام"

    def test_removes_other_tme_links_but_keeps_destination(self) -> None:
        text = "خبر @source پشتیبانی @support لینک t.me/adsch/55 مقصد @dest"
        result = rewrite_source_channel_mentions(text, ["@source"], "@dest")
        assert result == "خبر @dest پشتیبانی لینک مقصد @dest"

    def test_keeps_email_and_vless_userinfo(self) -> None:
        text = "ایمیل a@example.com کانفیگ vless://uuid@host:443 و @support"
        result = rewrite_source_channel_mentions(text, ["@source"], "@dest")
        assert result == "ایمیل a@example.com کانفیگ vless://uuid@host:443 و"

    def test_empty_destination_disables_rewrite(self) -> None:
        text = "سلام @source"
        result = rewrite_source_channel_mentions(text, ["@source"], "")
        assert result == text

    def test_rewrite_shifts_custom_emoji_entity_offsets(self) -> None:
        """Entity offsets after a rewritten mention remain aligned."""
        text = "خبر @source بعد *"
        entity = TextEntity(
            kind="custom_emoji",
            offset=text.index("*"),
            length=1,
            data={"document_id": 123456789},
        )

        result = rewrite_source_channel_mentions_with_entities(
            text,
            [entity],
            ["@source"],
            "@destination",
        )

        assert result.text == "خبر @destination بعد *"
        assert result.entities == [
            TextEntity(
                kind="custom_emoji",
                offset=result.text.index("*"),
                length=1,
                data={"document_id": 123456789},
            )
        ]

    def test_cleanup_shifts_custom_emoji_after_removed_mention(self) -> None:
        """Entity offsets shift after unrelated Telegram ids are removed."""
        text = "خبر @support بعد *"
        entity = TextEntity(
            kind="custom_emoji",
            offset=text.index("*"),
            length=1,
            data={"document_id": 123456789},
        )

        result = rewrite_source_channel_mentions_with_entities(
            text,
            [entity],
            ["@source"],
            "@destination",
        )

        assert result.text == "خبر بعد *"
        assert result.entities[0].offset == result.text.index("*")
