"""Unit tests for the approval and publishing workflow."""

from __future__ import annotations

import pytest

from src.application.approval_service import ApprovalService
from src.domain.entities import DestinationChannel, Post
from src.domain.enums import ChannelKind
from src.shared.errors import ApprovalStateError
from tests.unit.application.fakes import (
    FakeAdminRepository,
    FakeChannelRepository,
    FakePostRepository,
    FakePublisher,
    FakePublishLogRepository,
)

ADMIN_ID = 111
NON_ADMIN_ID = 999
NEWS_CHANNEL = -100200
VPN_CHANNEL = -100300


def _make_service(
    publisher: FakePublisher | None = None,
) -> tuple[ApprovalService, FakePostRepository, FakePublishLogRepository, FakePublisher]:
    """Build the approval service with fakes and one stored post."""
    posts = FakePostRepository()
    publish_log = FakePublishLogRepository()
    channels = FakeChannelRepository(
        [
            DestinationChannel(chat_id=NEWS_CHANNEL, title="News", kind=ChannelKind.NEWS),
            DestinationChannel(chat_id=VPN_CHANNEL, title="VPN", kind=ChannelKind.VPN),
        ]
    )
    admins = FakeAdminRepository({ADMIN_ID})
    pub = publisher or FakePublisher()
    service = ApprovalService(posts, publish_log, channels, admins, pub)
    return service, posts, publish_log, pub


def _post(post_id: str = "p1") -> Post:
    """Build a minimal stored post."""
    return Post(
        post_id=post_id,
        source_chat_id=-1,
        source_message_id=1,
        text="خبر برای انتشار",
        content_hash="h",
    )


class TestApprovalService:
    """Tests for :class:`ApprovalService`."""

    async def test_publish_records_log(self) -> None:
        service, posts, publish_log, publisher = _make_service()
        await posts.save(_post())
        message_id = await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert message_id > 0
        assert await publish_log.is_published("p1", NEWS_CHANNEL)
        assert publisher.posts == [(NEWS_CHANNEL, "p1")]

    async def test_duplicate_publish_to_same_channel_rejected(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        with pytest.raises(ApprovalStateError):
            await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)

    async def test_same_post_to_multiple_channels_allowed(self) -> None:
        service, posts, publish_log, _ = _make_service()
        await posts.save(_post())
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        await service.publish("p1", VPN_CHANNEL, ADMIN_ID)
        assert await publish_log.published_channels("p1") == {NEWS_CHANNEL, VPN_CHANNEL}

    async def test_non_admin_rejected(self) -> None:
        service, posts, _, publisher = _make_service()
        await posts.save(_post())
        with pytest.raises(ApprovalStateError):
            await service.publish("p1", NEWS_CHANNEL, NON_ADMIN_ID)
        assert publisher.posts == []

    async def test_missing_post_rejected(self) -> None:
        service, _, _, _ = _make_service()
        with pytest.raises(ApprovalStateError):
            await service.publish("missing", NEWS_CHANNEL, ADMIN_ID)

    async def test_unknown_channel_rejected(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        with pytest.raises(ApprovalStateError):
            await service.publish("p1", -12345, ADMIN_ID)

    async def test_request_approval_without_notifier_raises(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        with pytest.raises(ApprovalStateError):
            await service.request_approval("p1")
