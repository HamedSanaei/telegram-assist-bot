"""Unit tests for approval keyboard propagation."""

from __future__ import annotations

from src.application.approval_service import ApprovalService
from src.domain.entities import ApprovalMessageRef, DestinationChannel, Post
from src.presentation.approval_bot.propagation import (
    refresh_all_approval_keyboards,
    refresh_approval_keyboards,
)
from tests.unit.application.fakes import (
    FakeAdminRepository,
    FakeApprovalMessageRepository,
    FakeChannelRepository,
    FakePostRepository,
    FakePublisher,
    FakePublishLogRepository,
)


class FakeBot:
    """Fake aiogram bot that records edit requests and can fail one message."""

    def __init__(
        self,
        fail_message_id: int | None = None,
        not_modified_message_id: int | None = None,
        fail_error: str = "message not found",
    ) -> None:
        """Args: fail_message_id: Message id that raises as stale."""
        self.fail_message_id = fail_message_id
        self.not_modified_message_id = not_modified_message_id
        self.fail_error = fail_error
        self.edits: list[tuple[int, int]] = []

    async def edit_message_reply_markup(
        self, chat_id: int, message_id: int, reply_markup: object
    ) -> None:
        """Record a keyboard edit or raise for the configured message."""
        if message_id == self.not_modified_message_id:
            raise RuntimeError("Bad Request: message is not modified")
        if message_id == self.fail_message_id:
            raise RuntimeError(self.fail_error)
        self.edits.append((chat_id, message_id))


async def test_refresh_deactivates_failed_approval_message() -> None:
    """A stale approval message is marked inactive without stopping refresh."""
    posts = FakePostRepository()
    await posts.save(
        Post(
            post_id="p1",
            source_chat_id=-1001,
            source_message_id=1,
            text="خبر",
            content_hash="hash",
        )
    )
    approval_messages = FakeApprovalMessageRepository()
    await approval_messages.record_messages(
        [
            ApprovalMessageRef(
                post_id="p1", admin_user_id=1, chat_id=1, message_id=10
            ),
            ApprovalMessageRef(
                post_id="p1", admin_user_id=2, chat_id=2, message_id=20
            ),
        ]
    )
    service = ApprovalService(
        posts=posts,
        publish_log=FakePublishLogRepository(),
        channels=FakeChannelRepository([DestinationChannel(chat_id=-100, title="News")]),
        admins=FakeAdminRepository({1, 2}),
        publisher=FakePublisher(),
        approval_messages=approval_messages,
    )
    bot = FakeBot(fail_message_id=20)

    refreshed = await refresh_approval_keyboards(bot, service, "p1")

    assert refreshed == 1
    assert bot.edits == [(1, 10)]
    refs = await service.active_approval_messages("p1")
    assert [ref.message_id for ref in refs] == [10]


async def test_refresh_keeps_not_modified_approval_message_active() -> None:
    """Telegram's no-op edit response must not deactivate a valid message ref."""
    posts = FakePostRepository()
    await posts.save(
        Post(
            post_id="p1",
            source_chat_id=-1001,
            source_message_id=1,
            text="خبر",
            content_hash="hash",
        )
    )
    approval_messages = FakeApprovalMessageRepository()
    await approval_messages.record_messages(
        [ApprovalMessageRef(post_id="p1", admin_user_id=1, chat_id=1, message_id=10)]
    )
    service = ApprovalService(
        posts=posts,
        publish_log=FakePublishLogRepository(),
        channels=FakeChannelRepository([DestinationChannel(chat_id=-100, title="News")]),
        admins=FakeAdminRepository({1}),
        publisher=FakePublisher(),
        approval_messages=approval_messages,
    )
    bot = FakeBot(not_modified_message_id=10)

    refreshed = await refresh_approval_keyboards(bot, service, "p1")

    assert refreshed == 1
    refs = await service.active_approval_messages("p1")
    assert [ref.message_id for ref in refs] == [10]


async def test_refresh_deactivates_message_that_cannot_be_edited() -> None:
    """Telegram's permanent can't-edit response must not retry every restart."""
    posts = FakePostRepository()
    await posts.save(Post("p1", -1001, 1, "خبر", "hash"))
    approval_messages = FakeApprovalMessageRepository()
    await approval_messages.record_messages(
        [ApprovalMessageRef("p1", 1, 1, 10)]
    )
    service = ApprovalService(
        posts=posts,
        publish_log=FakePublishLogRepository(),
        channels=FakeChannelRepository([DestinationChannel(-100, "News")]),
        admins=FakeAdminRepository({1}),
        publisher=FakePublisher(),
        approval_messages=approval_messages,
    )

    await refresh_approval_keyboards(
        FakeBot(fail_message_id=10, fail_error="message can't be edited"),
        service,
        "p1",
    )

    assert await service.active_approval_messages("p1") == []


async def test_bulk_refresh_limits_posts_to_prevent_flooding() -> None:
    """Config reload refreshes only a bounded newest approval window."""
    posts = FakePostRepository()
    approval_messages = FakeApprovalMessageRepository()
    for index in range(3):
        post_id = f"p{index}"
        await posts.save(Post(post_id, -1001, index, "خبر", f"hash-{index}"))
        await approval_messages.record_messages(
            [ApprovalMessageRef(post_id, 1, 1, 10 + index)]
        )
    service = ApprovalService(
        posts=posts,
        publish_log=FakePublishLogRepository(),
        channels=FakeChannelRepository([DestinationChannel(-100, "News")]),
        admins=FakeAdminRepository({1}),
        publisher=FakePublisher(),
        approval_messages=approval_messages,
    )
    bot = FakeBot()

    refreshed = await refresh_all_approval_keyboards(
        bot,
        service,
        max_posts=2,
        delay_seconds=0,
    )

    assert refreshed == 2
    assert len(bot.edits) == 2
