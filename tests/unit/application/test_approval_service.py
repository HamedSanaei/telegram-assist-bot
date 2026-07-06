"""Unit tests for the approval and publishing workflow."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from src.application.approval_service import ApprovalService
from src.domain.entities import ApprovalMessageRef, DestinationChannel, Post, TextEntity
from src.domain.enums import ChannelKind
from src.shared.errors import ApprovalStateError, TelegramPublishError
from tests.unit.application.fakes import (
    FakeAdminRepository,
    FakeApprovalMessageRepository,
    FakeApprovalNotifier,
    FakeApprovalRequestRepository,
    FakeChannelRepository,
    FakePostRepository,
    FakePublisher,
    FakePublishLogRepository,
    FakeQueueRepository,
    FakeScheduledPublisher,
)

ADMIN_ID = 111
NON_ADMIN_ID = 999
NEWS_CHANNEL = -100200
VPN_CHANNEL = -100300
NEWS_INTERVAL_MINUTES = 30


def _make_service(
    publisher: FakePublisher | None = None,
    source_usernames: list[str] | None = None,
    news_public_id: str = "@news_dest",
    scheduled_publisher: FakeScheduledPublisher | None = None,
) -> tuple[ApprovalService, FakePostRepository, FakePublishLogRepository, FakePublisher]:
    """Build the approval service with fakes and one stored post."""
    posts = FakePostRepository()
    publish_log = FakePublishLogRepository()
    channels = FakeChannelRepository(
        [
            DestinationChannel(
                chat_id=NEWS_CHANNEL,
                title="News",
                public_id=news_public_id,
                kind=ChannelKind.NEWS,
                post_interval_minutes=NEWS_INTERVAL_MINUTES,
            ),
            DestinationChannel(
                chat_id=VPN_CHANNEL,
                title="VPN",
                public_id="@vpn_dest",
                kind=ChannelKind.VPN,
            ),
        ],
        source_usernames=source_usernames,
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
        scheduled_publisher=scheduled_publisher or FakeScheduledPublisher(),
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

    async def test_request_approval_is_idempotent_when_recorded(self) -> None:
        posts = FakePostRepository()
        publish_log = FakePublishLogRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        admins = FakeAdminRepository({ADMIN_ID})
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=publish_log,
            channels=channels,
            admins=admins,
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
        )
        await posts.save(_post())

        await service.request_approval("p1")
        await service.request_approval("p1")

        assert notifier.sent == [("p1", [NEWS_CHANNEL])]
        assert await approval_requests.has_requested("p1") is True

    async def test_request_approval_records_delivered_message_refs(self) -> None:
        posts = FakePostRepository()
        publish_log = FakePublishLogRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        admins = FakeAdminRepository({ADMIN_ID})
        notifier = FakeApprovalNotifier()
        approval_messages = FakeApprovalMessageRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=publish_log,
            channels=channels,
            admins=admins,
            publisher=FakePublisher(),
            notifier=notifier,
            approval_messages=approval_messages,
        )
        await posts.save(_post())

        await service.request_approval("p1")

        refs = await service.active_approval_messages("p1")
        assert len(refs) == 1
        assert refs[0].post_id == "p1"

    async def test_request_approval_does_not_resend_record_without_active_messages(
        self,
    ) -> None:
        """A recorded approval request is never resent automatically."""
        posts = FakePostRepository()
        publish_log = FakePublishLogRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        admins = FakeAdminRepository({ADMIN_ID})
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        approval_messages = FakeApprovalMessageRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=publish_log,
            channels=channels,
            admins=admins,
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
            approval_messages=approval_messages,
        )
        await posts.save(_post())
        await approval_requests.record_requested("p1")

        await service.request_approval("p1")

        assert notifier.sent == []
        assert len(await service.active_approval_messages("p1")) == 0

    async def test_repair_orphaned_approval_requests_does_not_resend_messages(
        self,
    ) -> None:
        """Startup repair never resends already requested approvals."""
        posts = FakePostRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        approval_messages = FakeApprovalMessageRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=FakePublishLogRepository(),
            channels=channels,
            admins=FakeAdminRepository({ADMIN_ID}),
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
            approval_messages=approval_messages,
        )
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        await approval_requests.record_requested("p1")
        await approval_requests.record_requested("p2")
        existing_ref = ApprovalMessageRef(
            post_id="p2", admin_user_id=ADMIN_ID, chat_id=ADMIN_ID, message_id=22
        )
        await approval_messages.record_messages([existing_ref])

        repaired = await service.repair_orphaned_approval_requests()

        assert repaired == 0
        assert notifier.sent == []
        refs = await service.active_approval_messages("p2")
        assert [ref.message_id for ref in refs] == [22]

    async def test_request_approval_does_not_resend_reserved_request(self) -> None:
        """A reserved approval request is treated as already in-flight."""
        posts = FakePostRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=FakePublishLogRepository(),
            channels=channels,
            admins=FakeAdminRepository({ADMIN_ID}),
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
            approval_messages=FakeApprovalMessageRepository(),
        )
        await posts.save(_post())
        assert await approval_requests.reserve_request("p1") is True

        await service.request_approval("p1")

        assert notifier.sent == []

    async def test_repair_skips_posts_with_any_delivery_record(self) -> None:
        """Startup repair does not resend posts already touched by publishing."""
        posts = FakePostRepository()
        publish_log = FakePublishLogRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        approval_messages = FakeApprovalMessageRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=publish_log,
            channels=channels,
            admins=FakeAdminRepository({ADMIN_ID}),
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
            approval_messages=approval_messages,
        )
        for post_id in ("published", "scheduled", "reserved", "removed"):
            await posts.save(_post(post_id))
            await approval_requests.record_requested(post_id)
        await publish_log.record_published("published", NEWS_CHANNEL, 1)
        await publish_log.try_reserve_publish("scheduled", NEWS_CHANNEL, "scheduled")
        await publish_log.mark_scheduled(
            "scheduled",
            NEWS_CHANNEL,
            2,
            datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        await publish_log.try_reserve_publish("reserved", NEWS_CHANNEL, "immediate")
        await publish_log.record_published("removed", NEWS_CHANNEL, 3)
        await publish_log.mark_removed("removed", NEWS_CHANNEL)

        repaired = await service.repair_orphaned_approval_requests()

        assert repaired == 0
        assert notifier.sent == []

    async def test_request_approval_skips_delivered_orphan(self) -> None:
        """Direct approval requests also avoid resending delivered posts."""
        posts = FakePostRepository()
        publish_log = FakePublishLogRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        approval_messages = FakeApprovalMessageRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=publish_log,
            channels=channels,
            admins=FakeAdminRepository({ADMIN_ID}),
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
            approval_messages=approval_messages,
        )
        await posts.save(_post())
        await approval_requests.record_requested("p1")
        await publish_log.record_published("p1", NEWS_CHANNEL, 1)

        await service.request_approval("p1")

        assert notifier.sent == []

    async def test_request_approval_retries_failed_reservation(self) -> None:
        """A failed approval dispatch can be retried by the queue item."""
        posts = FakePostRepository()
        channels = FakeChannelRepository(
            [DestinationChannel(chat_id=NEWS_CHANNEL, title="News")]
        )
        notifier = FakeApprovalNotifier()
        approval_requests = FakeApprovalRequestRepository()
        service = ApprovalService(
            posts=posts,
            publish_log=FakePublishLogRepository(),
            channels=channels,
            admins=FakeAdminRepository({ADMIN_ID}),
            publisher=FakePublisher(),
            notifier=notifier,
            approval_requests=approval_requests,
            approval_messages=FakeApprovalMessageRepository(),
        )
        await posts.save(_post())
        await approval_requests.mark_failed("p1", "telegram error")

        await service.request_approval("p1")

        assert notifier.sent == [("p1", [NEWS_CHANNEL])]
        assert await approval_requests.has_requested("p1") is True

    async def test_publish_rewrites_source_mentions_for_destination(self) -> None:
        service, posts, _, publisher = _make_service()
        await posts.save(_post())
        stored = await posts.get("p1")
        stored.text = "متن از @source و https://t.me/source/12"
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert publisher.post_texts == [
            (NEWS_CHANNEL, "متن از @news_dest و @news_dest")
        ]

    async def test_publish_rewrites_source_mentions_and_shifts_entities(self) -> None:
        """Custom emoji entity offsets follow destination mention rewrites."""
        service, posts, _, publisher = _make_service()
        post = _post()
        post.text = "از @source بعد *"
        post.text_entities = [
            TextEntity(
                kind="custom_emoji",
                offset=post.text.index("*"),
                length=1,
                data={"document_id": 123},
            )
        ]
        await posts.save(post)

        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)

        assert publisher.post_texts == [(NEWS_CHANNEL, "از @news_dest بعد *")]
        assert publisher.post_entities[0][1][0].offset == "از @news_dest بعد *".index("*")

    async def test_publish_rewrites_resolved_source_usernames(self) -> None:
        """Usernames resolved by the collector are rewritten too."""
        service, posts, _, publisher = _make_service(source_usernames=["alonews"])
        await posts.save(_post())
        stored = await posts.get("p1")
        stored.text = "عضو شوید: @AloNews و t.me/alonews/55"
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert publisher.post_texts == [
            (NEWS_CHANNEL, "عضو شوید: @news_dest و @news_dest")
        ]

    async def test_publish_without_public_id_keeps_text(self) -> None:
        """Without a destination public_id the text stays untouched."""
        service, posts, _, publisher = _make_service(
            source_usernames=["alonews"], news_public_id=""
        )
        await posts.save(_post())
        stored = await posts.get("p1")
        stored.text = "متن با @alonews"
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert publisher.post_texts == [(NEWS_CHANNEL, "متن با @alonews")]

    async def test_publish_failure_releases_reservation(self) -> None:
        failing_publisher = FakePublisher(fail=True)
        service, posts, publish_log, _ = _make_service(publisher=failing_publisher)
        await posts.save(_post())

        with pytest.raises(TelegramPublishError):
            await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)

        assert await publish_log.is_published("p1", NEWS_CHANNEL) is False

    async def test_existing_reservation_blocks_second_publish(self) -> None:
        service, posts, publish_log, publisher = _make_service()
        await posts.save(_post())
        assert await publish_log.try_reserve_publish(
            "p1", NEWS_CHANNEL, "immediate"
        )

        with pytest.raises(ApprovalStateError):
            await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)

        assert publisher.posts == []

    async def test_toggle_publish_second_click_deletes_message(self) -> None:
        service, posts, publish_log, publisher = _make_service()
        await posts.save(_post())

        first = await service.toggle_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        second = await service.toggle_publish("p1", NEWS_CHANNEL, ADMIN_ID)

        assert first.action == "published"
        assert second.action == "unpublished"
        assert publisher.deleted == [(NEWS_CHANNEL, first.message_id)]
        assert await publish_log.published_channels("p1") == set()

    async def test_toggle_publish_rejects_active_schedule(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post())
        await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)

        with pytest.raises(ApprovalStateError):
            await service.toggle_publish("p1", NEWS_CHANNEL, ADMIN_ID)


class TestSchedulePublish:
    """Tests for :meth:`ApprovalService.schedule_publish`."""

    async def test_first_scheduled_post_is_due_five_minutes_later(self) -> None:
        scheduled_publisher = FakeScheduledPublisher()
        service, posts, publish_log, _ = _make_service(
            scheduled_publisher=scheduled_publisher
        )
        await posts.save(_post())
        before = datetime.now(timezone.utc)
        scheduled_at = await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        assert scheduled_at >= before + timedelta(minutes=5)
        assert scheduled_at <= datetime.now(timezone.utc) + timedelta(minutes=5, seconds=5)
        assert scheduled_publisher.scheduled == [(NEWS_CHANNEL, "p1", scheduled_at)]
        assert await publish_log.scheduled_channels("p1") == {NEWS_CHANNEL}

    async def test_second_post_is_paced_by_five_minutes(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        first = await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        second = await service.schedule_publish("p2", NEWS_CHANNEL, ADMIN_ID)
        assert second == first + timedelta(minutes=5)

    async def test_pacing_counts_from_last_published_message(self) -> None:
        service, posts, publish_log, _ = _make_service()
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        await service.publish("p1", NEWS_CHANNEL, ADMIN_ID)
        last = await publish_log.last_published_at(NEWS_CHANNEL)
        scheduled_at = await service.schedule_publish("p2", NEWS_CHANNEL, ADMIN_ID)
        assert last is not None
        assert scheduled_at >= last + timedelta(minutes=5)

    async def test_pacing_is_independent_per_channel(self) -> None:
        service, posts, _, _ = _make_service()
        await posts.save(_post("p1"))
        await posts.save(_post("p2"))
        await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)
        before = datetime.now(timezone.utc)
        other = await service.schedule_publish("p2", VPN_CHANNEL, ADMIN_ID)
        assert other >= before + timedelta(minutes=5)

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

    async def test_schedule_without_native_scheduler_rejected(self) -> None:
        service, posts, _, _ = _make_service()
        service._scheduled_publisher = None
        await posts.save(_post())
        with pytest.raises(ApprovalStateError):
            await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)

    async def test_native_scheduled_history_is_used_for_next_slot(self) -> None:
        latest = datetime.now(timezone.utc) + timedelta(minutes=20)
        scheduled_publisher = FakeScheduledPublisher(latest=latest)
        service, posts, _, _ = _make_service(scheduled_publisher=scheduled_publisher)
        await posts.save(_post())

        scheduled_at = await service.schedule_publish("p1", NEWS_CHANNEL, ADMIN_ID)

        assert scheduled_at == latest + timedelta(minutes=5)

    async def test_toggle_schedule_second_click_deletes_native_schedule(self) -> None:
        scheduled_publisher = FakeScheduledPublisher()
        service, posts, publish_log, _ = _make_service(
            scheduled_publisher=scheduled_publisher
        )
        await posts.save(_post())

        first = await service.toggle_schedule("p1", NEWS_CHANNEL, ADMIN_ID)
        second = await service.toggle_schedule("p1", NEWS_CHANNEL, ADMIN_ID)

        assert first.action == "scheduled"
        assert second.action == "unscheduled"
        assert scheduled_publisher.deleted == [(NEWS_CHANNEL, 501)]
        assert await publish_log.scheduled_channels("p1") == set()
