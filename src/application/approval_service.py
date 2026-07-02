"""Use case: admin approval and publishing of collected posts."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

from src.application.channel_mention_rewriter import rewrite_source_channel_mentions
from src.domain.entities import DestinationChannel, Post
from src.domain.enums import QueueItemType
from src.domain.interfaces import (
    AdminRepository,
    ApprovalNotifier,
    ChannelRepository,
    MessagePublisher,
    PostRepository,
    PublishLogRepository,
    QueueRepository,
)
from src.shared.errors import ApprovalStateError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


class ApprovalService:
    """
    Coordinates the approval workflow for collected Telegram posts.

    This service sends posts to the approval bot, validates admin
    identity and approval state on every callback, publishes approved
    posts, and prevents duplicate publishing to the same channel.

    Example:
        service = ApprovalService(posts, publish_log, channels, admins, publisher)
        await service.publish(post_id, chat_id, admin_user_id)
    """

    def __init__(
        self,
        posts: PostRepository,
        publish_log: PublishLogRepository,
        channels: ChannelRepository,
        admins: AdminRepository,
        publisher: MessagePublisher,
        notifier: ApprovalNotifier | None = None,
        source_identifiers: list[str | int] | None = None,
        queue: QueueRepository | None = None,
    ) -> None:
        """
        Args:
            posts: Post repository.
            publish_log: Publish log used to block duplicate publishing.
            channels: Destination channel repository.
            admins: Admin repository used for authorization.
            publisher: Telegram publisher for destination channels.
            notifier: Approval bot notifier; optional so the service can
                be unit-tested without a running bot.
            source_identifiers: Source channel usernames/links from
                configuration. Mentions of these sources are replaced with
                the selected destination channel public id before publishing.
            queue: Background job queue; required for scheduled publishing
                (:meth:`schedule_publish`), optional otherwise.
        """
        self._posts = posts
        self._publish_log = publish_log
        self._channels = channels
        self._admins = admins
        self._publisher = publisher
        self._notifier = notifier
        self._source_identifiers = source_identifiers or []
        self._queue = queue

    async def request_approval(self, post_id: str) -> None:
        """
        Send the approval request message (with channel buttons) to admins.

        Args:
            post_id: Internal id of the post awaiting approval.

        Raises:
            ApprovalStateError: When the post no longer exists or no
                notifier is configured.
        """
        post = await self._get_post(post_id)
        if self._notifier is None:
            raise ApprovalStateError("No approval notifier configured")
        channels = await self._channels.list_destinations()
        await self._notifier.send_approval_request(post, channels)
        logger.info("Approval requested post=%s channels=%d", post_id, len(channels))

    async def ensure_admin(self, telegram_user_id: int) -> None:
        """
        Validate that a Telegram user is a configured admin.

        Args:
            telegram_user_id: The Telegram user id from the callback.

        Raises:
            ApprovalStateError: When the user is not an admin.
        """
        if not await self._admins.is_admin(telegram_user_id):
            raise ApprovalStateError(f"User {telegram_user_id} is not an admin")

    async def list_channels(self) -> list[DestinationChannel]:
        """Return all enabled destination channels for keyboard building."""
        return await self._channels.list_destinations()

    async def published_channels(self, post_id: str) -> set[int]:
        """Return chat ids the post has already been published to."""
        return await self._publish_log.published_channels(post_id)

    async def get_post(self, post_id: str) -> Post:
        """
        Return a post for preview, raising when it is gone.

        Raises:
            ApprovalStateError: When the post no longer exists (expired).
        """
        return await self._get_post(post_id)

    async def publish(self, post_id: str, chat_id: int, admin_user_id: int) -> int:
        """
        Publish an approved post to one destination channel.

        Validates admin identity, post existence, channel permission,
        and duplicate publishing state before sending.

        Args:
            post_id: Internal id of the approved post.
            chat_id: Destination channel chat id chosen by the admin.
            admin_user_id: Telegram user id of the approving admin.

        Returns:
            The Telegram message id of the published message.

        Raises:
            ApprovalStateError: When validation fails (not an admin, post
                missing, unknown channel, or already published).
            TelegramPublishError: When the Telegram send fails.
        """
        await self.ensure_admin(admin_user_id)
        post = await self._get_post(post_id)
        channels = await self._channels.list_destinations()
        channel = next((c for c in channels if c.chat_id == chat_id), None)
        if channel is None:
            raise ApprovalStateError(f"Unknown destination channel {chat_id}")
        if await self._publish_log.is_published(post_id, chat_id):
            raise ApprovalStateError(
                f"Post {post_id} already published to channel {chat_id}"
            )
        publish_post = self._post_for_destination(post, channel)
        message_id = await self._publisher.publish_post(chat_id, publish_post)
        await self._publish_log.record_published(post_id, chat_id, message_id)
        logger.info(
            "Published post=%s channel=%s message=%s admin=%s",
            post_id,
            chat_id,
            message_id,
            admin_user_id,
        )
        return message_id

    async def scheduled_channels(self, post_id: str) -> set[int]:
        """Return chat ids with a pending scheduled publish of this post."""
        if self._queue is None:
            return set()
        return await self._queue.scheduled_publish_channels(post_id)

    async def schedule_publish(
        self, post_id: str, chat_id: int, admin_user_id: int
    ) -> datetime:
        """
        Queue an approved post for paced publishing to one channel.

        Runs the same validations as :meth:`publish`, then enqueues a
        ``scheduled_publish`` job whose due time keeps at least
        ``post_interval_minutes`` (per destination channel) between the
        channel's last publish, its last queued slot, and this post.

        Args:
            post_id: Internal id of the approved post.
            chat_id: Destination channel chat id chosen by the admin.
            admin_user_id: Telegram user id of the approving admin.

        Returns:
            The UTC time the post is scheduled to be published at.

        Raises:
            ApprovalStateError: When validation fails (not an admin, post
                missing, unknown channel, already published, already
                queued for this channel, or no queue configured).
        """
        if self._queue is None:
            raise ApprovalStateError("No queue configured for scheduled publishing")
        await self.ensure_admin(admin_user_id)
        await self._get_post(post_id)
        channel = await self._channels.get_destination(chat_id)
        if channel is None or not channel.enabled:
            raise ApprovalStateError(f"Unknown destination channel {chat_id}")
        if await self._publish_log.is_published(post_id, chat_id):
            raise ApprovalStateError(
                f"Post {post_id} already published to channel {chat_id}"
            )
        if chat_id in await self._queue.scheduled_publish_channels(post_id):
            raise ApprovalStateError(
                f"Post {post_id} already scheduled for channel {chat_id}"
            )
        scheduled_at = await self._next_publish_slot(channel)
        await self._queue.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": post_id, "chat_id": chat_id, "admin_user_id": admin_user_id},
            scheduled_at=scheduled_at,
        )
        logger.info(
            "Scheduled publish post=%s channel=%s at=%s interval_min=%d admin=%s",
            post_id,
            chat_id,
            scheduled_at.isoformat(),
            channel.post_interval_minutes,
            admin_user_id,
        )
        return scheduled_at

    async def _next_publish_slot(self, channel: DestinationChannel) -> datetime:
        """
        Compute the next allowed publish time for a channel.

        The slot is ``interval`` minutes after the later of the channel's
        last publish and its last queued slot; when that lies in the past
        the post is due immediately.
        """
        now = datetime.now(timezone.utc)
        interval = timedelta(minutes=max(channel.post_interval_minutes, 0))
        candidates = [
            await self._publish_log.last_published_at(channel.chat_id),
            await self._queue.latest_scheduled_publish_for_channel(channel.chat_id),
        ]
        base = max((c for c in candidates if c is not None), default=None)
        if base is None:
            return now
        return max(now, base + interval)

    async def _get_post(self, post_id: str) -> Post:
        """Load a post or raise :class:`ApprovalStateError`."""
        post = await self._posts.get(post_id)
        if post is None:
            raise ApprovalStateError(f"Post {post_id} not found (possibly expired)")
        return post

    def _post_for_destination(self, post: Post, channel: DestinationChannel) -> Post:
        """
        Return a publish copy with source-channel mentions rewritten.

        Args:
            post: Stored original post.
            channel: Destination selected by the admin.

        Returns:
            A shallow copy of the post with text adjusted for the destination.
        """
        rewritten_text = rewrite_source_channel_mentions(
            post.text,
            self._source_identifiers,
            channel.public_id,
        )
        if rewritten_text == post.text:
            return post
        return replace(post, text=rewritten_text)
