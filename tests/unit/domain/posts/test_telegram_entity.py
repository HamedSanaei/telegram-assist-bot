"""Verify SDK-independent Telegram entity validation and preservation."""

from __future__ import annotations

from typing import cast

import pytest

from telegram_assist_bot.domain.posts import (
    InvalidTelegramEntityError,
    OriginalPostContent,
    TelegramEntity,
)


class _ExternalString(str):
    """Simulate a provider-owned string object with mutable attached state."""

    payload: list[str]

    def __new__(cls, value: str) -> _ExternalString:
        instance = super().__new__(cls, value)
        instance.payload = ["provider-owned"]
        return instance


@pytest.mark.parametrize("offset", [-1, True, False, 1.0, "1", None])
def test_entity_rejects_invalid_utf16_offsets(offset: object) -> None:
    with pytest.raises(InvalidTelegramEntityError) as error:
        TelegramEntity(
            offset_utf16=cast("int", offset),
            length_utf16=1,
            entity_type="bold",
        )

    assert error.value.field_name == "offset_utf16"
    assert error.value.rule == "must_be_non_negative_strict_integer"


@pytest.mark.parametrize("length", [0, -1, True, False, 1.0, "1", None])
def test_entity_rejects_invalid_utf16_lengths(length: object) -> None:
    with pytest.raises(InvalidTelegramEntityError) as error:
        TelegramEntity(
            offset_utf16=0,
            length_utf16=cast("int", length),
            entity_type="bold",
        )

    assert error.value.field_name == "length_utf16"
    assert error.value.rule == "must_be_positive_strict_integer"


@pytest.mark.parametrize("entity_type", ["", " ", "\n\t", "x" * 65, 1, None])
def test_entity_rejects_invalid_type_identifiers(entity_type: object) -> None:
    with pytest.raises(InvalidTelegramEntityError) as error:
        TelegramEntity(
            offset_utf16=0,
            length_utf16=1,
            entity_type=cast("str", entity_type),
        )

    assert error.value.field_name == "entity_type"
    assert error.value.rule == "must_be_non_blank_string_at_most_64_characters"


@pytest.mark.parametrize("custom_emoji_id", [None, "", " ", "\n", "x" * 257, 1])
def test_custom_emoji_requires_a_bounded_opaque_identifier(
    custom_emoji_id: object,
) -> None:
    with pytest.raises(InvalidTelegramEntityError) as error:
        TelegramEntity(
            offset_utf16=0,
            length_utf16=2,
            entity_type="custom_emoji",
            custom_emoji_id=cast("str | None", custom_emoji_id),
        )

    assert error.value.field_name == "custom_emoji_id"
    assert error.value.rule == "required_non_blank_string_at_most_256_characters"


def test_non_custom_entity_rejects_custom_emoji_metadata() -> None:
    with pytest.raises(InvalidTelegramEntityError) as error:
        TelegramEntity(
            offset_utf16=0,
            length_utf16=4,
            entity_type="bold",
            custom_emoji_id="5368324170671202286",
        )

    assert error.value.field_name == "custom_emoji_id"
    assert error.value.rule == "allowed_only_for_custom_emoji"


@pytest.mark.parametrize(
    ("entity_type", "custom_emoji_id"),
    [
        (_ExternalString("bold"), None),
        ("custom_emoji", _ExternalString("5368324170671202286")),
    ],
)
def test_entity_rejects_string_subclasses_with_external_payload(
    entity_type: str,
    custom_emoji_id: str | None,
) -> None:
    with pytest.raises(InvalidTelegramEntityError):
        TelegramEntity(
            offset_utf16=0,
            length_utf16=2,
            entity_type=entity_type,
            custom_emoji_id=custom_emoji_id,
        )


def test_regular_entity_keeps_exact_type_and_utf16_coordinates() -> None:
    entity = TelegramEntity(
        offset_utf16=7,
        length_utf16=12,
        entity_type="text_link",
    )

    assert entity.offset_utf16 == 7
    assert entity.length_utf16 == 12
    assert entity.entity_type == "text_link"
    assert entity.custom_emoji_id is None


def test_custom_emoji_metadata_and_utf16_coordinates_round_trip_exactly() -> None:
    custom_emoji_id = "5368324170671202286"
    entity = TelegramEntity(
        offset_utf16=5,
        length_utf16=2,
        entity_type="custom_emoji",
        custom_emoji_id=custom_emoji_id,
    )

    assert entity == TelegramEntity(5, 2, "custom_emoji", custom_emoji_id)
    assert entity.offset_utf16 == 5
    assert entity.length_utf16 == 2
    assert entity.custom_emoji_id == custom_emoji_id


def test_utf16_coordinates_are_not_reinterpreted_as_python_code_points() -> None:
    prefix = "سلام\n"
    displayed_emoji = "😀"
    text = f"{prefix}{displayed_emoji} پیام\u200cویژه"  # noqa: RUF001
    offset_utf16 = len(prefix.encode("utf-16-le")) // 2
    length_utf16 = len(displayed_emoji.encode("utf-16-le")) // 2
    entity = TelegramEntity(
        offset_utf16=offset_utf16,
        length_utf16=length_utf16,
        entity_type="custom_emoji",
        custom_emoji_id="5368324170671202286",
    )
    content = OriginalPostContent(
        text=text,
        caption=None,
        text_entities=(entity,),
    )

    assert offset_utf16 == 5
    assert length_utf16 == 2
    assert len(displayed_emoji) == 1
    assert content.text == "سلام\n😀 پیام\u200cویژه"  # noqa: RUF001
    assert content.text_entities[0] == entity


def test_text_and_caption_entities_remain_separate_and_in_source_order() -> None:
    first_text_entity = TelegramEntity(8, 4, "italic")
    second_text_entity = TelegramEntity(0, 4, "bold")
    caption_custom_emoji = TelegramEntity(
        9,
        2,
        "custom_emoji",
        "5368324170671202286",
    )
    content = OriginalPostContent(
        text="متن یک و متن دو",
        caption="توضیح و 😀",
        text_entities=(first_text_entity, second_text_entity),
        caption_entities=(caption_custom_emoji,),
    )

    assert content.text_entities == (first_text_entity, second_text_entity)
    assert content.caption_entities == (caption_custom_emoji,)
    assert id(content.text_entities) != id(content.caption_entities)


def test_persian_zwnj_line_breaks_and_emoji_are_preserved_byte_for_byte() -> None:
    text = "پیام\u200cویژه\nخط دوم 😀🟢"  # noqa: RUF001
    caption = "شرح فارسی\nبدون تغییر 👋🏽"
    content = OriginalPostContent(text=text, caption=caption)

    assert content.text == text
    assert content.caption == caption
    assert content.text is not None
    assert content.caption is not None
    assert content.text.encode("utf-8") == text.encode("utf-8")
    assert content.caption.encode("utf-8") == caption.encode("utf-8")
    assert content.text.count("\n") == 1
    assert content.caption.count("\n") == 1
    assert "\u200c" in content.text
