"""Use case for idempotent ingestion of one collected Telegram post.

Pipeline:
    source identity check -> exact duplicate check -> AI near-duplicate
    check -> AI classification -> VPN config extraction -> MongoDB storage
    -> immediate approval -> independent 20-minute score/VPN enrichment.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.application.ai_service import AiService
from src.domain.entities import MediaItem, Post, PostSourceMetrics, TextEntity, VpnConfig
from src.domain.enums import (
    IngestionMode,
    MediaDownloadStatus,
    PostCategory,
    QualityScoreStatus,
    QueueItemType,
    SourceMetricsStatus,
)
from src.domain.interfaces import PostRepository, QueueRepository
from src.domain.services.text_fingerprint import rank_similar_texts
from src.domain.services.text_normalizer import content_hash
from src.domain.services.vpn_parser import extract_vpn_configs
from src.shared.errors import (
    DuplicateDetectionError,
    PostClassificationError,
    VpnTextCleanupError,
)
from src.shared.logging_setup import get_logger

logger = get_logger(__name__)

QUALITY_SCORE_DELAY_MINUTES = 20
LOCAL_DUPLICATE_SCORE_THRESHOLD = 0.92
LOCAL_AI_CANDIDATE_LIMIT = 5


@dataclass(frozen=True)
class _StoredPostResult:
    """Result of an atomic first-write attempt for one source message."""

    post: Post
    inserted: bool


@dataclass(frozen=True)
class CollectedMessage:
    """
    Raw input describing one message or album collected from a source channel.

    Attributes:
        source_chat_id: Telegram chat id of the source channel.
        message_id: First Telegram message id for a single message or album.
        text: Message text or caption. It may be empty for media-only posts.
        grouped_id: Telegram album/group id, if available.
        media: Attachments downloaded by the collector.
        text_entities: Formatting entities captured from the source text.
        source_metrics: Telegram-side engagement metrics.
    """

    source_chat_id: int
    message_id: int
    text: str
    source_label: str = ""
    grouped_id: int | None = None
    media: list[MediaItem] = field(default_factory=list)
    expected_media_count: int = 0
    text_entities: list[TextEntity] = field(default_factory=list)
    source_metrics: PostSourceMetrics = field(default_factory=PostSourceMetrics)
    ingestion_mode: IngestionMode = IngestionMode.CONFIGURED_SOURCE


class CollectPostUseCase:
    """
    Coordinates storage, AI checks, and queue handoff for collected posts.

    The use case is restart-safe: if a source message is already stored but
    never reached an active queue stage, it repairs the missing stage instead
    of dropping the post.

    Example:
        use_case = CollectPostUseCase(posts, queue, ai)
        post = await use_case.handle_new_message(collected)
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
            posts: Post repository, backed by MongoDB in production.
            queue: SQLite queue repository.
            ai: AI service used for classification and near-deduplication.
            retention_days: Number of days before stored posts expire.
            recent_compare_limit: Number of recent post texts to compare.
            vpn_testing_enabled: Whether VPN config posts must pass the
                Iran worker before approval.
        """
        self._posts = posts
        self._queue = queue
        self._ai = ai
        self._retention_days = retention_days
        self._recent_compare_limit = recent_compare_limit
        self._vpn_testing_enabled = vpn_testing_enabled

    async def has_seen_source_message(
        self, source_chat_id: int, message_id: int, grouped_id: int | None = None
    ) -> bool:
        """
        Return whether a source message identity already exists in MongoDB.

        Args:
            source_chat_id: Telegram source chat id.
            message_id: Telegram source message id.
            grouped_id: Optional Telegram album/group id.

        Returns:
            ``True`` when the post is already stored.
        """
        return (
            await self._posts.find_by_source_message(
                source_chat_id, message_id, grouped_id
            )
            is not None
        )

    async def should_download_media(
        self,
        source_chat_id: int,
        message_id: int,
        grouped_id: int | None,
        expected_media_count: int,
    ) -> bool:
        """
        Return whether the collector should download media for a source message.

        Args:
            source_chat_id: Telegram source chat id.
            message_id: Telegram source message id.
            grouped_id: Optional Telegram album/group id.
            expected_media_count: Number of media messages in the current
                Telegram update/backfill batch.

        Returns:
            ``True`` when the source is new, or when MongoDB has an older
            copy with missing media that should be repaired.
        """
        if expected_media_count <= 0:
            return False
        existing = await self._posts.find_by_source_message(
            source_chat_id, message_id, grouped_id
        )
        if existing is None:
            return True
        return len(existing.media) < expected_media_count or not self._stored_media_exists(
            existing
        )

    async def handle_new_message(self, message: CollectedMessage) -> Post | None:
        """
        Process one collected message end to end.

        Args:
            message: The collected message.

        Returns:
            The stored :class:`Post`, or ``None`` when the message is empty.

        Raises:
            RepositoryError: When persistence fails.

        Side effects:
            Stores every non-empty source message in MongoDB, including
            duplicates and irrelevant posts, so restart backfills do not keep
            redownloading or reprocessing the same Telegram message.
        """
        text = (message.text or "").strip()
        if not text and not message.media:
            logger.info(
                "Skipping empty message chat=%s msg=%s",
                message.source_chat_id,
                message.message_id,
            )
            return None

        existing_source = await self._posts.find_by_source_message(
            message.source_chat_id,
            message.message_id,
            message.grouped_id,
        )
        if existing_source is not None:
            repaired_media = await self._repair_existing_media(
                existing_source, message
            )
            if await self._ensure_pipeline_queued(existing_source):
                logger.info(
                    "Repaired stored source post post=%s chat=%s msg=%s",
                    existing_source.post_id,
                    message.source_chat_id,
                    message.message_id,
                )
                return existing_source
            if repaired_media:
                return existing_source
            logger.info(
                "Skipping already processed source message chat=%s msg=%s",
                message.source_chat_id,
                message.message_id,
            )
            return None

        hash_source = text or f"media:{message.source_chat_id}:{message.message_id}"
        post_hash = content_hash(hash_source)
        existing_hash = await self._posts.find_by_content_hash(post_hash)
        if existing_hash is not None:
            duplicate = await self._store_skipped_post(
                message=message,
                post_hash=post_hash,
                category=existing_hash.category or PostCategory.GENERAL_NEWS,
                provider="exact_hash",
                skipped_reason="duplicate",
                is_duplicate=True,
                duplicate_of=existing_hash.post_id,
                duplicate_provider="exact_hash",
            )
            if await self._ensure_pipeline_queued(existing_hash):
                logger.info(
                    "Repaired original post for exact duplicate original=%s duplicate=%s",
                    existing_hash.post_id,
                    duplicate.post_id,
                )
            logger.info(
                "Stored exact duplicate chat=%s msg=%s duplicate_of=%s",
                message.source_chat_id,
                message.message_id,
                existing_hash.post_id,
            )
            return duplicate

        return await self._classify_and_store_new(message, post_hash, text)

    async def handle_vpn_discovery_message(
        self, message: CollectedMessage
    ) -> Post | None:
        """
        Store and immediately approve a config-bearing message from any dialog.

        The method performs source-message idempotency and config-level exact
        deduplication before invoking AI only to remove surrounding promotion.
        Quality scoring is intentionally not required for this ingestion mode.
        """
        existing = await self._posts.find_by_source_message(
            message.source_chat_id, message.message_id, message.grouped_id
        )
        if existing is not None:
            await self._ensure_pipeline_queued(existing)
            return None
        configs = extract_vpn_configs(message.text or "")
        if not configs:
            return None
        fingerprint_by_raw = {
            config.raw: self._vpn_fingerprint(config.raw) for config in configs
        }
        seen = await self._posts.find_seen_vpn_fingerprints(
            list(fingerprint_by_raw.values())
        )
        fresh_configs: list[VpnConfig] = []
        fresh_fingerprints: set[str] = set()
        for config in configs:
            fingerprint = fingerprint_by_raw[config.raw]
            if fingerprint in seen or fingerprint in fresh_fingerprints:
                continue
            fresh_fingerprints.add(fingerprint)
            fresh_configs.append(config)
        if not fresh_configs:
            skipped = replace(
                message,
                ingestion_mode=IngestionMode.DIALOG_VPN_DISCOVERY,
            )
            stored = await self._store_post(
                message=skipped,
                post_hash=content_hash(message.text or "vpn-duplicate"),
                category=PostCategory.VPN_CONFIG,
                provider="local_vpn_fingerprint",
                skipped_reason="duplicate_vpn_configs",
                is_duplicate=True,
                duplicate_of=None,
                duplicate_provider="local_vpn_fingerprint",
                configs=configs,
            )
            return stored.post
        cleaned_text, provider = await self._clean_discovery_text(
            message.text or "", configs, fresh_configs
        )
        discovery = replace(
            message,
            text=cleaned_text,
            text_entities=[],
            ingestion_mode=IngestionMode.DIALOG_VPN_DISCOVERY,
        )
        stored = await self._store_post(
            message=discovery,
            post_hash=content_hash(
                "\n".join(sorted(config.raw for config in fresh_configs))
            ),
            category=PostCategory.VPN_CONFIG,
            provider=provider,
            skipped_reason=None,
            is_duplicate=False,
            duplicate_of=None,
            duplicate_provider=None,
            configs=fresh_configs,
        )
        post = stored.post
        if not stored.inserted:
            await self._ensure_pipeline_queued(post)
            return None
        await self._enqueue_pipeline(post)
        logger.info(
            "Stored dialog VPN discovery post=%s chat=%s msg=%s configs=%d",
            post.post_id,
            message.source_chat_id,
            message.message_id,
            len(fresh_configs),
        )
        return post

    async def _clean_discovery_text(
        self,
        text: str,
        all_configs: list[VpnConfig],
        fresh_configs: list[VpnConfig],
    ) -> tuple[str, str]:
        """Protect fresh config URIs, clean ad text, and restore exact values."""
        fresh_raw = {config.raw for config in fresh_configs}
        protected = text
        for config in all_configs:
            if config.raw not in fresh_raw:
                protected = protected.replace(config.raw, "")
        placeholders: dict[str, str] = {}
        for index, config in enumerate(fresh_configs):
            placeholder = f"__VPN_CONFIG_{index}__"
            placeholders[placeholder] = config.raw
            protected = protected.replace(config.raw, placeholder)
        provider = "fallback_config_only"
        try:
            result = await self._ai.clean_vpn_post(protected)
            if all(placeholder in result.text for placeholder in placeholders):
                protected = result.text
                provider = result.provider
            else:
                protected = "\n".join(placeholders)
                logger.warning("VPN cleanup dropped protected placeholders; using fallback")
        except VpnTextCleanupError as exc:
            protected = "\n".join(placeholders)
            logger.warning("VPN cleanup unavailable; using config-only fallback error=%s", exc)
        for placeholder, raw in placeholders.items():
            protected = protected.replace(placeholder, raw)
        return protected.strip(), provider

    async def _classify_and_store_new(
        self, message: CollectedMessage, post_hash: str, text: str
    ) -> Post:
        """
        Classify and store a source message that has not been seen before.

        Args:
            message: The collected source message.
            post_hash: Exact content hash.
            text: Normalized post text.

        Returns:
            The stored post, including skipped duplicate/irrelevant posts.
        """
        category = PostCategory.GENERAL_NEWS
        provider_name: str | None = None
        is_duplicate = False
        duplicate_provider: str | None = None
        skipped_reason: str | None = None

        configs = extract_vpn_configs(text) if text else []
        if text:
            recent = await self._posts.list_recent_texts(self._recent_compare_limit)
            local_candidates = rank_similar_texts(
                text,
                recent,
                limit=LOCAL_AI_CANDIDATE_LIMIT,
            )
            if (
                local_candidates
                and local_candidates[0].score >= LOCAL_DUPLICATE_SCORE_THRESHOLD
            ):
                is_duplicate = True
                duplicate_provider = "local_fuzzy"
                skipped_reason = "duplicate"
                provider_name = "local_fuzzy"
                logger.info(
                    "Local fuzzy duplicate chat=%s msg=%s score=%.3f compared=%d",
                    message.source_chat_id,
                    message.message_id,
                    local_candidates[0].score,
                    len(recent),
                )
                stored = await self._store_post(
                    message=message,
                    post_hash=post_hash,
                    category=category,
                    provider=provider_name,
                    skipped_reason=skipped_reason,
                    is_duplicate=is_duplicate,
                    duplicate_of=None,
                    duplicate_provider=duplicate_provider,
                    configs=configs,
                )
                return stored.post
            compare_texts = [candidate.text for candidate in local_candidates]
            try:
                analysis = await self._ai.analyze_post(text, compare_texts)
                category = analysis.category
                provider_name = analysis.provider
                if analysis.is_duplicate:
                    is_duplicate = True
                    duplicate_provider = analysis.provider
                    skipped_reason = "duplicate"
                    logger.info(
                        "AI-detected duplicate chat=%s msg=%s provider=%s compared=%d",
                        message.source_chat_id,
                        message.message_id,
                        analysis.provider,
                        len(compare_texts),
                    )
                elif analysis.is_advertisement and not configs:
                    skipped_reason = "advertisement"
                    logger.info(
                        "AI-pruned advertisement chat=%s msg=%s provider=%s reason=%s",
                        message.source_chat_id,
                        message.message_id,
                        analysis.provider,
                        analysis.reason,
                    )
                elif analysis.is_advertisement and configs:
                    logger.info(
                        "Ignored advertisement label for VPN config chat=%s msg=%s "
                        "provider=%s configs=%d reason=%s",
                        message.source_chat_id,
                        message.message_id,
                        analysis.provider,
                        len(configs),
                        analysis.reason,
                    )
                else:
                    logger.info(
                        "Analyzed chat=%s msg=%s category=%s provider=%s compared=%d",
                        message.source_chat_id,
                        message.message_id,
                        category.value,
                        provider_name,
                        len(compare_texts),
                    )
                    if category == PostCategory.IRRELEVANT:
                        skipped_reason = "irrelevant"
            except (DuplicateDetectionError, PostClassificationError) as exc:
                logger.warning(
                    "Combined analysis unavailable; falling back chat=%s msg=%s error=%s",
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
                        skipped_reason = "irrelevant"
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

        if configs and category not in (PostCategory.VPN, PostCategory.VPN_CONFIG):
            category = PostCategory.VPN_CONFIG

        stored = await self._store_post(
            message=message,
            post_hash=post_hash,
            category=category,
            provider=provider_name,
            skipped_reason=skipped_reason,
            is_duplicate=is_duplicate,
            duplicate_of=None,
            duplicate_provider=duplicate_provider,
            configs=configs,
        )
        post = stored.post
        if not stored.inserted:
            await self._ensure_pipeline_queued(post)
            logger.info(
                "Source race resolved using stored winner post=%s chat=%s msg=%s",
                post.post_id,
                message.source_chat_id,
                message.message_id,
            )
            return post
        if skipped_reason:
            logger.info(
                "Stored skipped post id=%s chat=%s msg=%s reason=%s",
                post.post_id,
                message.source_chat_id,
                message.message_id,
                skipped_reason,
            )
            return post

        await self._enqueue_pipeline(post)
        logger.info(
            "Stored post id=%s chat=%s msg=%s category=%s configs=%d",
            post.post_id,
            message.source_chat_id,
            message.message_id,
            category.value,
            len(configs),
        )
        return post

    async def _store_skipped_post(
        self,
        message: CollectedMessage,
        post_hash: str,
        category: PostCategory,
        provider: str | None,
        skipped_reason: str,
        is_duplicate: bool,
        duplicate_of: str | None,
        duplicate_provider: str | None,
    ) -> Post:
        """Store a skipped source message without queueing any next stage."""
        stored = await self._store_post(
            message=message,
            post_hash=post_hash,
            category=category,
            provider=provider,
            skipped_reason=skipped_reason,
            is_duplicate=is_duplicate,
            duplicate_of=duplicate_of,
            duplicate_provider=duplicate_provider,
            configs=[],
        )
        return stored.post

    async def _store_post(
        self,
        message: CollectedMessage,
        post_hash: str,
        category: PostCategory,
        provider: str | None,
        skipped_reason: str | None,
        is_duplicate: bool,
        duplicate_of: str | None,
        duplicate_provider: str | None,
        configs: list[VpnConfig],
    ) -> _StoredPostResult:
        """Persist one normalized post document in MongoDB."""
        now = datetime.now(timezone.utc)
        post = Post(
            post_id=uuid.uuid4().hex,
            source_chat_id=message.source_chat_id,
            source_message_id=message.message_id,
            source_label=message.source_label,
            text=(message.text or "").strip(),
            text_entities=list(message.text_entities),
            content_hash=post_hash,
            ingestion_mode=message.ingestion_mode,
            quality_score_status=(
                QualityScoreStatus.NOT_REQUIRED
                if message.ingestion_mode == IngestionMode.DIALOG_VPN_DISCOVERY
                else QualityScoreStatus.PENDING
            ),
            source_metrics_status=(
                SourceMetricsStatus.NOT_REQUIRED
                if message.ingestion_mode == IngestionMode.DIALOG_VPN_DISCOVERY
                else SourceMetricsStatus.PENDING
            ),
            vpn_fingerprints=[self._vpn_fingerprint(config.raw) for config in configs],
            grouped_id=message.grouped_id,
            media=list(message.media),
            expected_media_count=message.expected_media_count,
            media_download_status=self._media_download_status(message),
            category=category,
            ai_provider=provider,
            is_duplicate=is_duplicate,
            duplicate_of=duplicate_of,
            duplicate_provider=duplicate_provider,
            skipped_reason=skipped_reason,
            source_metrics=message.source_metrics,
            vpn_configs=configs,
            collected_at=now,
            expires_at=now + timedelta(days=self._retention_days),
        )
        inserted = await self._posts.insert_if_absent(post)
        if inserted:
            logger.info("Saved post to MongoDB id=%s", post.post_id)
            return _StoredPostResult(post=post, inserted=True)
        existing = await self._posts.find_by_source_message(
            message.source_chat_id,
            message.message_id,
            message.grouped_id,
        )
        if existing is None:
            raise RuntimeError(
                "Mongo source identity conflict occurred but winner was not found"
            )
        return _StoredPostResult(post=existing, inserted=False)

    async def _repair_existing_media(
        self, existing: Post, message: CollectedMessage
    ) -> bool:
        """
        Add newly downloaded media to an already stored source post.

        Args:
            existing: Existing MongoDB post found by source identity.
            message: Current collector input, possibly with downloaded media.

        Returns:
            ``True`` when the stored post was updated.
        """
        if not message.media:
            return False
        if len(existing.media) >= len(message.media) and self._stored_media_exists(
            existing
        ):
            return False
        existing.media = list(message.media)
        existing.expected_media_count = message.expected_media_count
        existing.media_download_status = self._media_download_status(message)
        if message.text_entities:
            existing.text_entities = list(message.text_entities)
        existing.source_metrics = message.source_metrics
        await self._posts.save(existing)
        logger.info(
            "Repaired stored source media post=%s chat=%s msg=%s media=%d",
            existing.post_id,
            message.source_chat_id,
            message.message_id,
            len(message.media),
        )
        return True

    @staticmethod
    def _media_download_status(message: CollectedMessage) -> MediaDownloadStatus:
        """Return download completeness for one normalized source message."""
        expected = max(0, message.expected_media_count)
        downloaded = len(message.media)
        if expected == 0 or downloaded >= expected:
            return MediaDownloadStatus.COMPLETE
        if downloaded == 0:
            return MediaDownloadStatus.FAILED
        return MediaDownloadStatus.PARTIAL

    @staticmethod
    def _stored_media_exists(post: Post) -> bool:
        """Return whether all stored media paths still exist on disk."""
        if not post.media:
            return False
        for media in post.media:
            if not media.file_path or not Path(media.file_path).exists():
                return False
        return True

    async def _ensure_pipeline_queued(self, post: Post) -> bool:
        """
        Requeue a stored post that never reached an active pipeline stage.

        Same-day backfill may see posts saved in MongoDB before a crash, code
        bug, or queue failure. Exact hash dedup must not hide those posts
        forever; this method restarts the missing next stage once.
        """
        if post.skipped_reason:
            return False
        repaired = False
        if await self._queue.enqueue_if_missing_post_item(
            QueueItemType.APPROVAL_REQUEST,
            post.post_id,
            {"post_id": post.post_id},
        ) is not None:
            repaired = True
        if post.quality_score_status == QualityScoreStatus.PENDING:
            if post.source_metrics_status == SourceMetricsStatus.PENDING:
                if await self._queue.enqueue_if_missing_post_item(
                    QueueItemType.SOURCE_METRICS_REFRESH,
                    post.post_id,
                    {"post_id": post.post_id},
                    scheduled_at=self._source_metrics_due_at(post),
                ) is not None:
                    repaired = True
            elif not await self._queue.has_active_or_successful_post_item(
                post.post_id, {QueueItemType.QUALITY_SCORE}
            ) and await self._queue.enqueue_if_missing_post_item(
                    QueueItemType.QUALITY_SCORE_UPDATE,
                    post.post_id,
                    {"post_id": post.post_id, "metrics_ready": True},
                ) is not None:
                repaired = True
        if (
            post.vpn_configs
            and self._vpn_testing_enabled
            and await self._queue.enqueue_if_missing_post_item(
                QueueItemType.VPN_TEST,
                post.post_id,
                {"post_id": post.post_id},
            )
            is not None
        ):
            repaired = True
        return repaired

    async def _enqueue_pipeline(self, post: Post) -> None:
        """Enqueue immediate approval and independent background enrichment."""
        changed = await self._ensure_pipeline_queued(post)
        logger.info(
            "Ensured post pipeline post=%s changed=%s score_status=%s vpn_configs=%d",
            post.post_id,
            changed,
            post.quality_score_status.value,
            len(post.vpn_configs),
        )

    @staticmethod
    def _source_metrics_due_at(post: Post) -> datetime:
        """
        Return when the post should be scored.

        Posts younger than 20 minutes wait until source engagement metrics
        have had time to settle. Older backfill posts are scored immediately.
        """
        now = datetime.now(timezone.utc)
        source_published_at = post.source_metrics.source_published_at
        if source_published_at is None:
            collected_at = post.collected_at or now
            if collected_at.tzinfo is None:
                collected_at = collected_at.replace(tzinfo=timezone.utc)
            return max(now, collected_at + timedelta(minutes=QUALITY_SCORE_DELAY_MINUTES))
        if source_published_at.tzinfo is None:
            source_published_at = source_published_at.replace(tzinfo=timezone.utc)
        else:
            source_published_at = source_published_at.astimezone(timezone.utc)
        due_at = source_published_at + timedelta(minutes=QUALITY_SCORE_DELAY_MINUTES)
        return max(now, due_at)

    @staticmethod
    def _vpn_fingerprint(raw: str) -> str:
        """Return a stable exact fingerprint for one proxy configuration URI."""
        return content_hash(raw.strip())
