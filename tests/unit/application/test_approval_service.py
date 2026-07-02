"""Unit tests for the approval and publishing workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.application.approval_service import ApprovalService
from src.domain.entities import DestinationChannel, Post
from src.domain.enums import ChannelKind, QueueItemType
from src.shared.errors import ApprovalStateError
from tests.unit.application.fakes import (
    FakeAdminRepository,
    FakeChannelRepository,
    FakePostRepository,
    FakePublisher,
    FakePublishLogRepository,
    FakeQueueRepository,
)

ADMIN_ID = 111
NON_ADMIN_ID = 999
NEWS_CHANNEL = -100200
VPN_CHANNEL = -100300
NEWS_INTERVAL_MINUTES = 30


def _make_service(
    publisher: FakePublisher | None = None,
) -> tuple[ApprovalService, FakePostRepository, FakePublishLogRepository, FakePublisher]:
    """Build the approval service with fakes and one stored post."""
    posts = FakePostRepository()
    publish_log = FakePublishLogRepository()
    channels = FakeChannelRepository(
        [
            DestinationChannel(
                chat_id=NEWS_CHANNEL,
                title="News",
                public_id="@news_dest",
                kind=ChannelKind.NEWS,
                post_interval_minutes=NEWS_INTERVAL_MINUTES,
            ),
            DestinationChannel(
                chat_id=VPN_CHANNEL,
                title="VPN",
                public_id="@vpn_dest",
                kind=ChannelKind.VPN,
            ),
        ]
    )
    admins = FakeAdminRepository({ADMIN_ID})
    pub = publisher or FakePublisher()
    service = ApprovalService(
        posts,
        publish_log,
        channels,
        admins,
        pub,
        source_identifiers=["@source"],
        queue=FakeQueueRepository(),
    )
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

    async def test_publish_rewrites_source_mentions_for_destination(self) -> None:
        service, posts, _, publisher = _make_service()
        await posts.save(_post())
        stored = await posts.get("p1")
        stored.text = "متن از @source و https://t.me/source/12"
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert publisher.post_texts == [
            (NEWS_CHANNEL, "متن از @news_dest و @news_dest")
        ]


class TestSchedulePublish:
    """Tests for :meth:`ApprovalService.schedule_publish`."""

    async def test_first_scheduled_post_is_due_immediately(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        before = datetime.now(timezone.utc)
        scheduled_at = await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert before <= scheduled_at <= datetime.now(timezone.utc)
        queue: FakeQueueRepository = service._queue
        assert queue.items[0].type == QueueItemType.SCHEDULED_PUBLISH
        assert queue.items[0].payload == {
            "post_id": "p1",
            "chat_id": NEWS_CHANNEL,
            "admin_user_id": ADMIN_ID,
        }

    async def test_second_post_is_paced_by_channel_interval(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        first = await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        second = await service.schedule_publish("p2", NEWS_CHANNEL, ADMIN_ID)
        assert second == first + timedelta(minutes=NEWS_INTERVAL_MINUTES)

    async def test_pacing_counts_from_last_published_message(self) -> None:
        service, posts, publish_log, _ = _make_service()
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        last = await publish_log.last_published_at(NEWS_CHANNEL)
        scheduled_at = await service.schedule_publish("p2", NEWS_CHANNEL, ADMIN_ID)
        assert scheduled_at == last + timedelta(minutes=NEWS_INTERVAL_MINUTES)

    async def test_pacing_is_independent_per_channel(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        before = datetime.now(timezone.utc)
        other = await service.schedule_publish("p2", VPN_CHANNEL, ADMIN_ID)
        assert before <= other <= datetime.now(timezone.utc)

    async def test_double_schedule_same_channel_rejected(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        with pytest.raises(ApprovalStateError):
            await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert await service.scheduled_channels("p1") == {NEWS_CHANNEL}

    async def test_schedule_after_publish_rejected(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        with pytest.raises(ApprovalStateError):
            await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)

    async def test_non_admin_cannot_schedule(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        with pytest.raises(ApprovalStateError):
            await service.schedule_publish("p1", NEWS_CHANNEL, NON_ADMIN_ID)

    async def test_schedule_without_queue_rejected(self) -> None:
        service, posts, _, _ = _make_service()
        service._queue = None
        await posts.save(_post())
        with pytest.raises(ApprovalStateError):
            await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
