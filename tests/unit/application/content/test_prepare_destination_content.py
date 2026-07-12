"""Verify destination pruning and UTF-16 entity rebasing."""

from telegram_assist_bot.application.prepare_destination_content import (
    prepare_destination_content,
)
from telegram_assist_bot.domain.posts import TelegramEntity


def test_source_replace_destination_protect_links_and_persian() -> None:
    text = "😀 سلام‌ @source_name https://t.me/other_name/12?q=1 @dest_name https://example.com/x"
    entity = TelegramEntity(0, 2, "custom_emoji", "99")
    result = prepare_destination_content(
        text=text,
        entities=(entity,),
        source_username="source_name",
        destination_username="Dest_Name",
    )
    assert result.text == "😀 سلام‌ @Dest_Name @dest_name https://example.com/x"
    assert result.entities == (entity,)


def test_intersecting_entity_removed_unknown_unaffected_preserved() -> None:
    text = "@other_name متن 😀"
    removed = TelegramEntity(0, 11, "unknown_future")
    emoji = TelegramEntity(16, 2, "custom_emoji", "1")
    result = prepare_destination_content(
        text=text,
        entities=(removed, emoji),
        source_username="source_name",
        destination_username="dest_name",
    )
    assert result.text == " متن 😀"
    assert result.entities[0].entity_type == "custom_emoji"
    assert result.entities[0].offset_utf16 == 5


def test_multiple_links_long_input_and_immutability() -> None:
    original = (
        "متن‌\n@one_name https://telegram.me/two_name?q=1 😀 "
        "https://safe.example/" + "الف" * 10000
    )
    entities = (TelegramEntity(0, 3, "unknown_future"),)
    result = prepare_destination_content(
        text=original,
        entities=entities,
        source_username="source_name",
        destination_username="dest_name",
    )
    assert "one_name" not in result.text
    assert "two_name" not in result.text
    assert "https://safe.example/" in result.text
    assert result.entities == entities
    assert original.startswith("متن‌\n@one_name")
