from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from telegram_assist_bot.infrastructure.telegram.user import (
    InvalidTelegramMessageError,
    map_telethon_message,
)


class MessageEntityBold:
    def __init__(self, offset: int, length: int) -> None:
        self.offset = offset
        self.length = length


class MessageEntityCustomEmoji:
    def __init__(self, offset: int, length: int, document_id: int) -> None:
        self.offset = offset
        self.length = length
        self.document_id = document_id


class DocumentAttributeSticker: ...


class DocumentAttributeVideo: ...


class DocumentAttributeAnimated: ...


class DocumentAttributeAudio:
    def __init__(self, *, voice: bool) -> None:
        self.voice = voice


class DocumentAttributeFilename:
    def __init__(self, file_name: str) -> None:
        self.file_name = file_name


def test_maps_persian_text_zwnj_emoji_and_utf16_entities_exactly() -> None:
    source = "سلام‌دنیا\n😀"
    raw = SimpleNamespace(
        id=7,
        date=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
        message=source,
        entities=[MessageEntityBold(0, 4), MessageEntityCustomEmoji(11, 2, 987654)],
        media=None,
        action=None,
    )

    mapped = map_telethon_message(
        raw,
        source_channel_id=-1001,
        source_channel_username="source_fixture",
        source_channel_display_name="منبع فارسی",
    )

    assert mapped.text == source
    assert mapped.caption is None
    assert mapped.text_entities[0].offset_utf16 == 0
    assert mapped.text_entities[1].entity_type == "custom_emoji"
    assert mapped.text_entities[1].custom_emoji_id == "987654"


def test_media_text_maps_to_caption_without_normalization() -> None:
    raw = SimpleNamespace(
        id=8,
        date=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
        message="کپشن‌اصلی\n✨",
        entities=[],
        media=object(),
        action=None,
    )

    mapped = map_telethon_message(
        raw,
        source_channel_id=-1001,
        source_channel_username=None,
        source_channel_display_name="Source",
    )

    assert mapped.text is None
    assert mapped.caption == "کپشن‌اصلی\n✨"
    assert mapped.has_media is True
    assert mapped.media == ()


@pytest.mark.parametrize(
    ("attributes", "mime_type", "expected"),
    [
        ([DocumentAttributeSticker()], "image/webp", "Sticker"),
        ([DocumentAttributeVideo()], "video/mp4", "Video"),
        (
            [DocumentAttributeVideo(), DocumentAttributeAnimated()],
            "video/mp4",
            "Animation",
        ),
        ([DocumentAttributeAudio(voice=True)], "audio/ogg", "Voice"),
        ([DocumentAttributeAudio(voice=False)], "audio/mpeg", "Audio"),
        ([], "application/pdf", "Document"),
    ],
)
def test_maps_document_media_metadata(
    attributes: list[object], mime_type: str, expected: str
) -> None:
    document = SimpleNamespace(
        mime_type=mime_type,
        size=42,
        attributes=[*attributes, DocumentAttributeFilename("فایل 😀.bin")],
    )
    raw = SimpleNamespace(
        id=11,
        date=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
        message="کپشن",
        entities=[],
        media=object(),
        document=document,
        photo=None,
        grouped_id=987,
        action=None,
    )
    mapped = map_telethon_message(
        raw,
        source_channel_id=-1001,
        source_channel_username=None,
        source_channel_display_name="Source",
    )
    assert mapped.media[0].media_type.value == expected
    assert mapped.media[0].size_bytes == 42
    assert mapped.media[0].original_filename == "فایل 😀.bin"
    assert mapped.media[0].media_group_id == "987"


def test_maps_photo_size_and_reference() -> None:
    raw = SimpleNamespace(
        id=12,
        date=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
        message=None,
        entities=[],
        media=object(),
        document=None,
        photo=SimpleNamespace(
            sizes=[SimpleNamespace(size=10), SimpleNamespace(size=20)]
        ),
        grouped_id=None,
        action=None,
    )
    mapped = map_telethon_message(
        raw,
        source_channel_id=-1001,
        source_channel_username=None,
        source_channel_display_name="Source",
    )
    assert mapped.media[0].media_type.value == "Photo"
    assert mapped.media[0].size_bytes == 20
    assert mapped.media[0].opaque_reference == "-1001:12:0"


def test_rejects_naive_source_timestamp() -> None:
    raw = SimpleNamespace(
        id=9,
        date=datetime(2026, 7, 11, 9, 0, tzinfo=UTC).replace(tzinfo=None),
        message="text",
        entities=[],
        media=None,
        action=None,
    )

    with pytest.raises(InvalidTelegramMessageError):
        map_telethon_message(
            raw,
            source_channel_id=-1001,
            source_channel_username=None,
            source_channel_display_name="Source",
        )


@pytest.mark.parametrize(
    ("overrides"),
    [
        {"id": 0},
        {"message": 123},
        {"entities": "not-an-entity-sequence"},
        {"entities": [MessageEntityCustomEmoji(0, 2, 0)]},
        {"entities": [MessageEntityBold(-1, 2)]},
    ],
)
def test_rejects_invalid_sdk_scalar_and_entity_shapes(
    overrides: dict[str, object],
) -> None:
    values: dict[str, object] = {
        "id": 9,
        "date": datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
        "message": "text",
        "entities": [],
        "media": None,
        "action": None,
    }
    values.update(overrides)

    with pytest.raises(InvalidTelegramMessageError):
        map_telethon_message(
            SimpleNamespace(**values),
            source_channel_id=-1001,
            source_channel_username=None,
            source_channel_display_name="Source",
        )


def test_action_message_is_marked_as_service_without_payload_change() -> None:
    raw = SimpleNamespace(
        id=10,
        date=datetime(2026, 7, 11, 9, 0, tzinfo=UTC),
        message="service text",
        entities=[],
        media=None,
        action=object(),
    )

    mapped = map_telethon_message(
        raw,
        source_channel_id=-1001,
        source_channel_username=None,
        source_channel_display_name="Source",
    )

    assert mapped.is_service is True
    assert mapped.text == "service text"
