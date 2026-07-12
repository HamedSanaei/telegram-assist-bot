"""Verify media readiness and ordered album publication requests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from tests.unit.application.publication.test_publish_text_immediately import (
    NOW,
    Publisher,
    Repository,
    request,
    service,
)

from telegram_assist_bot.application.ports import PublicationMedia, PublicationPayload
from telegram_assist_bot.application.publication import PublishStatus
from telegram_assist_bot.domain.media import MediaType


@pytest.mark.parametrize("media_type", list(MediaType))
def test_accepts_each_supported_ready_media_type(media_type: MediaType) -> None:
    repository, publisher = Repository(), Publisher()
    media = PublicationMedia(media_type, "safe/item.bin", NOW + timedelta(days=1))
    payload = PublicationPayload(-1002, "کپشن\u200cفارسی🙂", (), (media,))  # noqa: RUF001
    result = asyncio.run(
        service(repository, publisher).execute(request(payload=payload))
    )
    assert result.status is PublishStatus.SUCCEEDED
    assert publisher.payloads[0].media == (media,)


def test_preserves_album_member_order() -> None:
    repository, publisher = Repository(), Publisher()
    expires = NOW + timedelta(days=1)
    media = tuple(
        PublicationMedia(MediaType.PHOTO, f"safe/{index}.jpg", expires)
        for index in (3, 1, 2)
    )
    payload = PublicationPayload(-1002, "آلبوم", (), media)
    result = asyncio.run(
        service(repository, publisher).execute(request(payload=payload))
    )
    assert result.status is PublishStatus.SUCCEEDED
    assert [item.storage_path for item in publisher.payloads[0].media] == [
        "safe/3.jpg",
        "safe/1.jpg",
        "safe/2.jpg",
    ]


@pytest.mark.parametrize(
    "media",
    [
        PublicationMedia(MediaType.PHOTO, "safe/expired", NOW),
        PublicationMedia(
            MediaType.PHOTO,
            "safe/not-ready",
            datetime(2026, 7, 13, tzinfo=UTC),
            ready=False,
        ),
    ],
)
def test_rejects_expired_or_not_ready_media(media: PublicationMedia) -> None:
    repository, publisher = Repository(), Publisher()
    payload = PublicationPayload(-1002, None, (), (media,))
    result = asyncio.run(
        service(repository, publisher).execute(request(payload=payload))
    )
    assert result.status is PublishStatus.REJECTED
    assert publisher.payloads == []
