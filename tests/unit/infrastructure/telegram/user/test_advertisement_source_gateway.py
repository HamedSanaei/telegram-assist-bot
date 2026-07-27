# mypy: disable-error-code="union-attr"
"""Telethon advertisement-source mapping and failure boundary tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from telegram_assist_bot.application.ports import (
    AdvertisementSourceGroupDTO,
    AdvertisementSourceNotFoundError,
    AdvertisementSourceTransientError,
)
from telegram_assist_bot.infrastructure.telegram.user.advertisement_source_gateway import (  # noqa: E501
    TelethonAdvertisementSourceGateway,
    _entity_type,
    _map_entity,
    _map_telethon_msg_to_dto,
)

NOW = datetime(2026, 7, 27, 12, tzinfo=UTC)
PhotoMedia = type("MessageMediaPhoto", (), {})
DocumentMedia = type("MessageMediaDocument", (), {})
Filename = type("DocumentAttributeFilename", (), {})
Bold = type("MessageEntityBold", (), {})
TextUrl = type("MessageEntityTextUrl", (), {})
CustomEmoji = type("MessageEntityCustomEmoji", (), {})
DEFAULT_ENTITY = SimpleNamespace(id=-1001)


def _message(
    identifier: int,
    *,
    text: str = "متن",
    media: object | None = None,
    grouped_id: int | None = None,
) -> object:
    document = None
    if type(media).__name__ == "MessageMediaDocument":
        filename = Filename()
        filename.file_name = "file.pdf"
        document = SimpleNamespace(
            mime_type="application/pdf",
            size_bytes=12,
            attributes=(filename,),
        )
    entity = Bold()
    entity.offset = 0
    entity.length = 2
    return SimpleNamespace(
        id=identifier,
        date=NOW,
        edit_date=NOW,
        message=text,
        entities=(entity,),
        grouped_id=grouped_id,
        media=media,
        document=document,
    )


class Client:
    def __init__(
        self,
        *,
        message: object | BaseException | None,
        surrounding: object | BaseException | None = None,
        entity: object | BaseException = DEFAULT_ENTITY,
    ) -> None:
        self.message = message
        self.surrounding = surrounding
        self.entity = entity
        self.calls = 0

    async def get_entity(self, entity: object) -> object:
        assert entity == "channel"
        if isinstance(self.entity, BaseException):
            raise self.entity
        return self.entity

    async def get_messages(self, entity: object, **kwargs: object) -> object:
        del entity
        self.calls += 1
        value = self.message if "ids" in kwargs else self.surrounding
        if isinstance(value, BaseException):
            raise value
        return value


def test_entity_mapping_covers_metadata_and_invalid_values() -> None:
    assert _entity_type(Bold()) == "bold"
    assert _entity_type(type("MessageEntity", (), {})()) == "unknown"

    bold = Bold()
    bold.offset = 0
    bold.length = 2
    assert _map_entity(bold) is not None

    custom = CustomEmoji()
    custom.offset = 0
    custom.length = 2
    custom.document_id = 99
    assert _map_entity(custom).custom_emoji_id == "99"

    link = TextUrl()
    link.offset = 0
    link.length = 2
    link.url = "https://example.invalid"
    assert _map_entity(link).url == "https://example.invalid"

    invalid = Bold()
    invalid.offset = "0"
    invalid.length = 2
    assert _map_entity(invalid) is None

    out_of_bounds = Bold()
    out_of_bounds.offset = -1
    out_of_bounds.length = 2
    assert _map_entity(out_of_bounds) is None


def test_message_mapping_text_photo_document_and_invalid_records() -> None:
    text = _map_telethon_msg_to_dto(_message(1), "channel", -1001)
    assert text is not None
    assert text.text == "متن"
    assert text.caption is None
    assert text.text_entities

    photo = _map_telethon_msg_to_dto(
        _message(2, media=PhotoMedia(), grouped_id=77), "channel", -1001
    )
    assert photo is not None
    assert photo.text is None
    assert photo.caption == "متن"
    assert photo.media[0].opaque_reference == "-1001:2:0"

    document = _map_telethon_msg_to_dto(
        _message(3, media=DocumentMedia()), "channel", -1001
    )
    assert document is not None
    assert document.media[0].original_filename == "file.pdf"
    assert document.media[0].size_bytes == 12

    assert _map_telethon_msg_to_dto(SimpleNamespace(id=0), "channel", -1001) is None
    assert (
        _map_telethon_msg_to_dto(
            SimpleNamespace(id=1, date="invalid"), "channel", -1001
        )
        is None
    )


def test_gateway_fetches_single_and_sorted_media_group() -> None:
    async def scenario() -> None:
        single_client = Client(message=_message(5))
        single = await TelethonAdvertisementSourceGateway(
            single_client
        ).fetch_advertisement_post("@channel", 5)
        assert single.source_message_id == 5

        main = _message(10, media=PhotoMedia(), grouped_id=77)
        group_client = Client(
            message=main,
            surrounding=[
                _message(11, text="", media=PhotoMedia(), grouped_id=77),
                _message(9, media=PhotoMedia(), grouped_id=77),
                _message(8, media=PhotoMedia(), grouped_id=88),
            ],
        )
        group = await TelethonAdvertisementSourceGateway(
            group_client
        ).fetch_advertisement_post("channel", 10)
        assert isinstance(group, AdvertisementSourceGroupDTO)
        assert [item.source_message_id for item in group.members] == [9, 10, 11]
        assert group.canonical_caption == "متن"

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("client", "message"),
    [
        (Client(message=None), "does not exist"),
        (Client(message=SimpleNamespace(empty=True)), "does not exist"),
        (Client(message=SimpleNamespace(id=0, date=NOW)), "Failed to map"),
    ],
)
def test_gateway_rejects_missing_or_unmappable_message(
    client: Client, message: str
) -> None:
    async def scenario() -> None:
        with pytest.raises(AdvertisementSourceNotFoundError, match=message):
            await TelethonAdvertisementSourceGateway(client).fetch_advertisement_post(
                "channel", 1
            )

    asyncio.run(scenario())


def test_gateway_maps_resolve_fetch_and_group_failures_to_safe_transient_errors() -> (
    None
):
    async def scenario() -> None:
        with pytest.raises(AdvertisementSourceTransientError, match="resolve"):
            await TelethonAdvertisementSourceGateway(
                Client(message=None, entity=RuntimeError("sensitive"))
            ).fetch_advertisement_post("channel", 1)

        with pytest.raises(AdvertisementSourceTransientError, match="fetch"):
            await TelethonAdvertisementSourceGateway(
                Client(message=RuntimeError("sensitive"))
            ).fetch_advertisement_post("channel", 1)

        with pytest.raises(AdvertisementSourceTransientError, match="media group"):
            await TelethonAdvertisementSourceGateway(
                Client(
                    message=_message(1, media=PhotoMedia(), grouped_id=77),
                    surrounding=RuntimeError("sensitive"),
                )
            ).fetch_advertisement_post("channel", 1)

    asyncio.run(scenario())
