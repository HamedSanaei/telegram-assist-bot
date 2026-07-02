"""Use case: process one newly collected message from a source channel.

Pipeline: normalize -> exact-hash dedup -> AI near-duplicate check ->
AI classification -> VPN config extraction -> store in MongoDB with a
14-day expiry -> enqueue the follow-up job (VPN test or approval).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from src.application.ai_service import AiService
from src.domain.entities import MediaItem, Post
from src.domain.enums import PostCategory, QueueItemType
from src.domain.interfaces import PostRepository, QueueRepository
from src.domain.services.text_normalizer import content_hash
from src.domain.services.vpn_parser import extract_vpn_configs
from src.shared.errors import DuplicateDetectionError, PostClassificationError
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class CollectedMessage:
    """
    Raw input describing one message collected from a source channel.

    Attributes:
        source_chat_id: Telegram chat id of the source channel.
        message_id: Telegram message id in the source channel.
        text: Message text (may be empty for media-only posts).
        media: Media attachments already downloaded to local storage.
    """

    source_chat_id: int
    message_id: int
    text: str
    media: list[MediaItem] = field(default_factory=list)


class CollectPostUseCase:
    """
    Orchestrates deduplication, classification, storage, and queueing
    for newly collected posts.

    Example:
        use_case = CollectPostUseCase(posts, queue, ai)
        post = await use_case.handle_new_message(message)
    """

    def __init__(
        self,
        posts: PostRepository,
        queue: QueueRepository,
        ai: AiService,
        retention_days: int = 14,
        recent_compare_limit: int = 30,
        vpn_testing_enabled: bool = True,
    ) -> None:
        """
        Args:
            posts: Post repository (MongoDB in production).
            queue: Background job queue repository (SQLite).
            ai: AI service used for dedup and classification.
            retention_days: How many days posts are kept before expiry.
            recent_compare_limit: How many recent posts the AI duplicate
                check compares against.
            vpn_testing_enabled: When ``False``, posts containing VPN
                configs skip the Iran test and go straight to approval.
        """
        self._posts = posts
        self._queue = queue
        self._ai = ai
        self._retention_days = retention_days
        self._recent_compare_limit = recent_compare_limit
        self._vpn_testing_enabled = vpn_testing_enabled

    async def handle_new_message(self, message: CollectedMessage) -> Post | None:
        """
        Process one collected message end to end.

        Args:
            message: The collected message.

        Returns:
            The stored :class:`Post`, or ``None`` when the message was
            skipped (empty, duplicate, or irrelevant).

        Raises:
            RepositoryError: When persistence fails.

        Notes:
            Temporary AI failures are logged, but they no longer drop the
            post. The post is stored with a conservative default category
            so an administrator can review it manually.
        """
        text = (message.text or "").strip()
        if not text and not message.media:
            logger.info(
                "Skipping empty message chat=%s msg=%s",
                message.source_chat_id,
                message.message_id,
            )
            return None

        hash_source = text or f"media:{message.source_chat_id}:{message.message_id}"
        post_hash = content_hash(hash_source)
        if await self._posts.find_by_content_hash(post_hash) is not None:
            logger.info(
                "Skipping exact duplicate chat=%s msg=%s",
                message.source_chat_id,
                message.message_id,
            )
            return None

        category = PostCategory.GENERAL_NEWS
        provider_name: str | None = None
        if text:
            recent = await self._posts.list_recent_texts(self._recent_compare_limit)
            if recent:
                try:
                    dup = await self._ai.is_duplicate(text, recent)
                    if dup.is_duplicate:
                        logger.info(
                            "Skipping AI-detected duplicate chat=%s msg=%s provider=%s",
                            message.source_chat_id,
                            message.message_id,
                            dup.provider,
                        )
                        return None
                    logger.info(
                        "Duplicate check passed chat=%s msg=%s provider=%s compared=%d",
                        message.source_chat_id,
                        message.message_id,
                        dup.provider,
                        len(recent),
                    )
                except DuplicateDetectionError as exc:
                    logger.warning(
                        "Duplicate check unavailable; continuing chat=%s msg=%s error=%s",
                        message.source_chat_id,
                        message.message_id,
                        exc,
                    )
            try:
                classification = await self._ai.classify_post(text)
                category = classification.category
                provider_name = classification.provider
                logger.info(
                    "Classified chat=%s msg=%s category=%s provider=%s",
                    message.source_chat_id,
                    message.message_id,
                    category.value,
                    provider_name,
                )
                if category == PostCategory.IRRELEVANT:
                    logger.info(
                        "Skipping irrelevant post chat=%s msg=%s provider=%s",
                        message.source_chat_id,
                        message.message_id,
                        provider_name,
                    )
                    return None
            except PostClassificationError as exc:
                provider_name = "unavailable"
                logger.error(
                    "Classification unavailable; storing for manual approval "
                    "chat=%s msg=%s default_category=%s error=%s",
                    message.source_chat_id,
                    message.message_id,
                    category.value,
                    exc,
                )

        configs = extract_vpn_configs(text) if text else []
        if configs and category not in (PostCategory.VPN, PostCategory.VPN_CONFIG):
            category = PostCategory.VPN_CONFIG

        now = datetime.now(timezone.utc)
        post = Post(
            post_id=uuid.uuid4().hex,
            source_chat_id=message.source_chat_id,
            source_message_id=message.message_id,
            text=text,
            content_hash=post_hash,
            media=list(message.media),
            category=category,
            ai_provider=provider_name,
            vpn_configs=configs,
            collected_at=now,
            expires_at=now + timedelta(days=self._retention_days),
        )
        await self._posts.save(post)
        logger.info("Saved post to MongoDB id=%s", post.post_id)

        if configs and self._vpn_testing_enabled:
            next_step = QueueItemType.VPN_TEST
        else:
            next_step = QueueItemType.APPROVAL_REQUEST
        await self._queue.enqueue(next_step, {"post_id": post.post_id})
        logger.info("Enqueued %s post=%s", next_step.value, post.post_id)
        logger.info(
            "Stored post id=%s chat=%s msg=%s category=%s configs=%d",
            post.post_id,
            message.source_chat_id,
            message.message_id,
            category.value,
            len(configs),
        )
        return post
