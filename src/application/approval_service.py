"""Use case: admin approval and publishing of collected posts."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone

from src.application.channel_mention_rewriter import (
    rewrite_source_channel_mentions_with_entities,
)
from src.domain.entities import (
    ApprovalMessageRef,
    ApprovalPreviewRefreshResult,
    DestinationChannel,
    Post,
    PublishLogEntry,
)
from src.domain.enums import ChannelKind, IngestionMode
from src.domain.interfaces import (
    AdminRepository,
    ApprovalMessageRepository,
    ApprovalRequestRepository,
    ApprovalNotifier,
    ChannelRepository,
    MessagePublisher,
    PostRepository,
    PublishLogRepository,
    QueueRepository,
    ScheduledMessagePublisher,
)
from src.shared.errors import ApprovalStateError, TelegramPublishError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class ApprovalToggleResult:
    """
    Result of one direct approval-button toggle.

    Attributes:
        action: Short action name: ``published``, ``unpublished``,
            ``scheduled``, or ``unscheduled``.
        message_id: Telegram message id when an action created one.
        scheduled_at: Native Telegram schedule time for scheduled actions.
    """

    action: str
    message_id: int | None = None
    scheduled_at: datetime | None = None


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
        approval_requests: ApprovalRequestRepository | None = None,
        approval_messages: ApprovalMessageRepository | None = None,
        scheduled_publisher: ScheduledMessagePublisher | None = None,
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
            approval_requests: Optional repository that prevents sending
                the same approval preview more than once.
            approval_messages: Optional repository storing approval-bot
                message ids for multi-admin keyboard propagation.
            scheduled_publisher: Optional Telethon user-session publisher
                used for native Telegram channel scheduling.
        """
        self._posts = posts
        self._publish_log = publish_log
        self._channels = channels
        self._admins = admins
        self._publisher = publisher
        self._notifier = notifier
        self._source_identifiers = source_identifiers or []
        self._queue = queue
        self._approval_requests = approval_requests
        self._approval_messages = approval_messages
        self._scheduled_publisher = scheduled_publisher

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
        reserved = True
        if self._approval_requests is not None:
            reserved = await self._approval_requests.reserve_request(post_id)
            if not reserved:
                logger.info(
                    "Skipping approval resend; approval already requested post=%s",
                    post_id,
                )
                return
        channels = await self._channels_for_post(post)
        try:
            message_refs = await self._notifier.send_approval_request(post, channels)
        except Exception as exc:
            if self._approval_requests is not None:
                await self._approval_requests.mark_failed(post_id, str(exc))
            raise
        if self._approval_messages is not None:
            await self._approval_messages.record_messages(message_refs)
        if self._approval_requests is not None:
            await self._approval_requests.mark_sent(post_id)
        logger.info("Approval requested post=%s channels=%d", post_id, len(channels))

    async def repair_orphaned_approval_requests(self) -> int:
        """
        Report aggregate approval idempotency state without resending posts.

        Returns:
            Always ``0``. Approval resend is intentionally disabled because
            ``approval_requests`` is the idempotency boundary.

        Side effects:
            Emits one summary log instead of scanning and logging every
            historical approval request separately.
        """
        if self._approval_requests is None:
            return 0
        requested = await self._approval_requests.list_requested_post_ids()
        logger.info(
            "Approval resend diagnostic requested=%d automatic_resend=disabled",
            len(requested),
        )
        return 0

    async def active_approval_messages(self, post_id: str) -> list[ApprovalMessageRef]:
        """Return active approval-bot message references for a post."""
        if self._approval_messages is None:
            return []
        return await self._approval_messages.list_active(post_id)

    async def active_approval_post_ids(self, limit: int | None = None) -> list[str]:
        """Return newest post ids that still have active approval messages."""
        if self._approval_messages is None:
            return []
        return await self._approval_messages.list_active_post_ids(limit)

    async def approval_view_state(
        self, post_id: str
    ) -> tuple[Post, list[DestinationChannel], set[int], set[int], list[PublishLogEntry]]:
        """Return the complete current state needed to render an approval UI."""
        post = await self._get_post(post_id)
        return (
            post,
            await self._channels_for_post(post),
            await self._publish_log.published_channels(post_id),
            await self._publish_log.scheduled_channels(post_id),
            await self._publish_log.list_history(post_id),
        )

    async def refresh_approval_previews(
        self,
        post_id: str,
        refs: list[ApprovalMessageRef] | None = None,
    ) -> ApprovalPreviewRefreshResult:
        """Refresh existing previews without creating any new Telegram message."""
        if self._notifier is None:
            return ApprovalPreviewRefreshResult()
        post, channels, published, scheduled, history = await self.approval_view_state(
            post_id
        )
        return await self._notifier.refresh_approval_request(
            post,
            channels,
            published,
            scheduled,
            bool(history),
            refs,
        )

    async def repair_recent_approval_previews(
        self,
        hours: int = 24,
        limit: int = 500,
        delay_seconds: float = 0.0,
    ) -> ApprovalPreviewRefreshResult:
        """Best-effort repair recent inactive refs without resending approvals.

        Args:
            hours: Age window for inactive references.
            limit: Maximum references inspected in one repair pass.
            delay_seconds: Optional throttle between post groups.

        Returns:
            Aggregated in-place edit result.
        """
        if self._approval_messages is None or self._notifier is None:
            return ApprovalPreviewRefreshResult()
        since = datetime.now(timezone.utc) - timedelta(hours=max(1, hours))
        grouped: dict[str, list[ApprovalMessageRef]] = {}
        for ref in await self._approval_messages.list_recent_inactive(since, limit):
            grouped.setdefault(ref.post_id, []).append(ref)
        totals = ApprovalPreviewRefreshResult()
        grouped_items = list(grouped.items())
        for index, (post_id, refs) in enumerate(grouped_items):
            try:
                result = await self.refresh_approval_previews(post_id, refs)
            except ApprovalStateError:
                continue
            totals = ApprovalPreviewRefreshResult(
                updated=totals.updated + result.updated,
                retryable_failures=(
                    totals.retryable_failures + result.retryable_failures
                ),
                permanent_failures=(
                    totals.permanent_failures + result.permanent_failures
                ),
            )
            if delay_seconds > 0 and index + 1 < len(grouped_items):
                await asyncio.sleep(delay_seconds)
        if grouped:
            logger.info(
                "Approval preview startup repair refs=%d updated=%d retryable=%d permanent=%d",
                sum(len(refs) for refs in grouped.values()),
                totals.updated,
                totals.retryable_failures,
                totals.permanent_failures,
            )
        return totals

    async def set_approval_message_mode(
        self, post_id: str, chat_id: int, message_id: int, delivery_mode: str
    ) -> None:
        """Persist the delivery mode selected on one approval message."""
        if self._approval_messages is None:
            return
        await self._approval_messages.set_delivery_mode(
            post_id, chat_id, message_id, delivery_mode
        )

    async def deactivate_approval_message(self, message_ref_id: int) -> None:
        """Mark an approval message as inactive after Telegram edit failure."""
        if self._approval_messages is None:
            return
        await self._approval_messages.deactivate(message_ref_id)

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

    async def _channels_for_post(self, post: Post) -> list[DestinationChannel]:
        """Return enabled destinations allowed for one post ingestion mode."""
        channels = await self._channels.list_destinations()
        if post.ingestion_mode != IngestionMode.DIALOG_VPN_DISCOVERY:
            return channels
        vpn_channels = [
            channel for channel in channels if channel.kind == ChannelKind.VPN
        ]
        if not vpn_channels:
            raise ApprovalStateError(
                "No enabled VPN destination exists for dialog discovery post"
            )
        return vpn_channels

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
        if not await self._publish_log.try_reserve_publish(
            post_id, chat_id, "immediate"
        ):
            raise ApprovalStateError(
                f"Post {post_id} already reserved or published to channel {chat_id}"
            )
        publish_post = await self._post_for_destination(post, channel)
        try:
            message_id = await self._publisher.publish_post(chat_id, publish_post)
        except TelegramPublishError:
            await self._publish_log.release_reservation(post_id, chat_id)
            raise
        await self._publish_log.mark_published(post_id, chat_id, message_id)
        logger.info(
            "Published post=%s channel=%s message=%s admin=%s",
            post_id,
            chat_id,
            message_id,
            admin_user_id,
        )
        return message_id

    async def toggle_publish(
        self, post_id: str, chat_id: int, admin_user_id: int
    ) -> ApprovalToggleResult:
        """
        Toggle immediate publishing for a post/channel pair.

        If the post is not active for the channel, it is published
        immediately. If it is already published in immediate mode, the real
        Telegram channel message is deleted and the state is marked removed.

        Args:
            post_id: Internal collected post id.
            chat_id: Destination channel chat id.
            admin_user_id: Telegram user id of the acting admin.

        Returns:
            The performed toggle action.

        Raises:
            ApprovalStateError: When another active mode exists or validation
                fails.
            TelegramPublishError: When Telegram publish/delete fails.
        """
        record = await self._publish_log.get_active_record(post_id, chat_id)
        if record is not None:
            if record.mode != "immediate" or record.status != "published":
                raise ApprovalStateError(
                    f"Post {post_id} has active {record.mode}/{record.status} "
                    f"state for channel {chat_id}"
                )
            if record.message_id is None:
                raise ApprovalStateError(
                    f"Published post {post_id} channel {chat_id} has no message id"
                )
            await self.ensure_admin(admin_user_id)
            await self._publisher.delete_message(chat_id, record.message_id)
            await self._publish_log.mark_removed(post_id, chat_id)
            logger.info(
                "Deleted published post=%s channel=%s message=%s admin=%s",
                post_id,
                chat_id,
                record.message_id,
                admin_user_id,
            )
            return ApprovalToggleResult(action="unpublished")
        message_id = await self.publish(post_id, chat_id, admin_user_id)
        return ApprovalToggleResult(action="published", message_id=message_id)

    async def scheduled_channels(self, post_id: str) -> set[int]:
        """Return chat ids with an active native scheduled publish."""
        return await self._publish_log.scheduled_channels(post_id)

    async def delivery_history(self, post_id: str) -> list[PublishLogEntry]:
        """Return persisted delivery history for one approval post."""
        return await self._publish_log.list_history(post_id)

    async def schedule_publish(
        self, post_id: str, chat_id: int, admin_user_id: int
    ) -> datetime:
        """
        Upload an approved post into Telegram's native channel schedule.

        Runs the same validations as :meth:`publish`, computes a paced slot
        five minutes after the latest scheduled/published post for that
        destination, uploads the post through the Telethon user session, and
        records the result so the same post cannot be scheduled twice.

        Args:
            post_id: Internal id of the approved post.
            chat_id: Destination channel chat id chosen by the admin.
            admin_user_id: Telegram user id of the approving admin.

        Returns:
            The UTC time the post is scheduled to be published at.

        Raises:
            ApprovalStateError: When validation fails (not an admin, post
            missing, unknown channel, already published, already scheduled,
            or no scheduled publisher configured).
        """
        if self._scheduled_publisher is None:
            raise ApprovalStateError("No native Telegram scheduler configured")
        await self.ensure_admin(admin_user_id)
        post = await self._get_post(post_id)
        channel = await self._channels.get_destination(chat_id)
        if channel is None or not channel.enabled:
            raise ApprovalStateError(f"Unknown destination channel {chat_id}")
        if await self._publish_log.is_published(post_id, chat_id):
            raise ApprovalStateError(
                f"Post {post_id} already published to channel {chat_id}"
            )
        active = await self._publish_log.get_active_record(post_id, chat_id)
        if active is not None:
            raise ApprovalStateError(
                f"Post {post_id} already has active {active.mode}/{active.status} "
                f"state for channel {chat_id}"
            )
        if not await self._publish_log.try_reserve_publish(
            post_id, chat_id, "scheduled"
        ):
            raise ApprovalStateError(
                f"Post {post_id} already reserved or published to channel {chat_id}"
            )
        scheduled_at = await self._next_publish_slot(channel)
        publish_post = await self._post_for_destination(post, channel)
        try:
            message_id = await self._scheduled_publisher.schedule_post(
                chat_id, publish_post, scheduled_at
            )
        except TelegramPublishError:
            await self._publish_log.release_reservation(post_id, chat_id)
            raise
        await self._publish_log.mark_scheduled(
            post_id, chat_id, message_id, scheduled_at
        )
        logger.info(
            "Native Telegram scheduled post=%s channel=%s message=%s at=%s admin=%s",
            post_id,
            chat_id,
            message_id,
            scheduled_at.isoformat(),
            admin_user_id,
        )
        return scheduled_at

    async def toggle_schedule(
        self, post_id: str, chat_id: int, admin_user_id: int
    ) -> ApprovalToggleResult:
        """
        Toggle native Telegram scheduling for a post/channel pair.

        If the post is not active for the channel, it is uploaded into the
        destination channel's native schedule. If it is already scheduled, the
        scheduled Telegram message is deleted and the state is marked removed.
        """
        record = await self._publish_log.get_active_record(post_id, chat_id)
        if record is not None:
            if record.mode != "scheduled" or record.status != "scheduled":
                raise ApprovalStateError(
                    f"Post {post_id} has active {record.mode}/{record.status} "
                    f"state for channel {chat_id}"
                )
            if self._scheduled_publisher is None:
                raise ApprovalStateError("No native Telegram scheduler configured")
            if record.message_id is None:
                raise ApprovalStateError(
                    f"Scheduled post {post_id} channel {chat_id} has no message id"
                )
            await self.ensure_admin(admin_user_id)
            await self._scheduled_publisher.delete_scheduled_message(
                chat_id, record.message_id
            )
            await self._publish_log.mark_removed(post_id, chat_id)
            logger.info(
                "Deleted scheduled post=%s channel=%s message=%s admin=%s",
                post_id,
                chat_id,
                record.message_id,
                admin_user_id,
            )
            return ApprovalToggleResult(action="unscheduled")
        scheduled_at = await self.schedule_publish(post_id, chat_id, admin_user_id)
        return ApprovalToggleResult(action="scheduled", scheduled_at=scheduled_at)

    async def _next_publish_slot(self, channel: DestinationChannel) -> datetime:
        """
        Compute the next allowed publish time for a channel.

        The slot is five minutes after the later of the channel's last
        recorded publish, last internal scheduled item (legacy safety), and
        latest native Telegram scheduled message when available.
        """
        now = datetime.now(timezone.utc)
        earliest = now + timedelta(minutes=5)
        interval = timedelta(minutes=5)
        native_latest = (
            await self._scheduled_publisher.latest_scheduled_at(channel.chat_id)
            if self._scheduled_publisher is not None
            else None
        )
        candidates = [
            await self._publish_log.last_published_at(channel.chat_id),
            await self._queue.latest_scheduled_publish_for_channel(channel.chat_id)
            if self._queue is not None
            else None,
            native_latest,
        ]
        base = max((c for c in candidates if c is not None), default=None)
        if base is None:
            return earliest
        return max(earliest, base + interval)

    async def _get_post(self, post_id: str) -> Post:
        """Load a post or raise :class:`ApprovalStateError`."""
        post = await self._posts.get(post_id)
        if post is None:
            raise ApprovalStateError(f"Post {post_id} not found (possibly expired)")
        return post

    async def _post_for_destination(
        self, post: Post, channel: DestinationChannel
    ) -> Post:
        """
        Return a publish copy with source-channel mentions rewritten.

        Mentions of both the configured source identifiers and the
        resolved public usernames of all enabled source channels (stored
        by the collector) are replaced with the destination's public id.

        Args:
            post: Stored original post.
            channel: Destination selected by the admin.

        Returns:
            A shallow copy of the post with text adjusted for the destination.

        Side effects:
            Logs a warning when the destination has no ``public_id``,
            because mentions cannot be rewritten without one.
        """
        if not channel.public_id.strip():
            logger.warning(
                "Destination channel=%s has no public_id; source mentions in "
                "post=%s are NOT rewritten. Set it via: /setdest %s public_id @channel",
                channel.chat_id,
                post.post_id,
                channel.chat_id,
            )
            return post
        identifiers: list[str | int] = list(self._source_identifiers)
        identifiers.extend(await self._channels.list_sources())
        identifiers.extend(
            f"@{username}" for username in await self._channels.list_source_usernames()
        )
        rewritten = rewrite_source_channel_mentions_with_entities(
            post.text,
            post.text_entities,
            identifiers,
            channel.public_id,
        )
        rewritten_text = rewritten.text
        if (
            post.ingestion_mode == IngestionMode.DIALOG_VPN_DISCOVERY
            and channel.public_id.strip().lower() not in rewritten_text.lower()
        ):
            rewritten_text = f"{rewritten_text.rstrip()}\n\n{channel.public_id.strip()}"
        if rewritten.text == post.text and rewritten.entities == post.text_entities:
            if rewritten_text == post.text:
                return post
        logger.info(
            "Rewrote source mentions post=%s channel=%s", post.post_id, channel.chat_id
        )
        return replace(post, text=rewritten_text, text_entities=rewritten.entities)
