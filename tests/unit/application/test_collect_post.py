"""Unit tests for the idempotent post collection pipeline."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.application.ai_service import AiService
from src.application.collect_post import CollectedMessage, CollectPostUseCase
from src.domain.entities import MediaItem, Post, PostSourceMetrics
from src.domain.enums import (
    IngestionMode,
    MediaDownloadStatus,
    MediaKind,
    PostCategory,
    QualityScoreStatus,
    QueueItemType,
)
from src.domain.services.text_normalizer import content_hash
from tests.unit.application.fakes import (
    FakeAiProvider,
    FakePostRepository,
    FakeQueueRepository,
)

VMESS_URI = "vmess://" + base64.urlsafe_b64encode(
    json.dumps(
        {"add": "example.com", "port": "443", "id": "uuid-1", "net": "tcp"}
    ).encode()
).decode()


def _use_case(
    posts: FakePostRepository,
    queue: FakeQueueRepository,
    category: PostCategory = PostCategory.GENERAL_NEWS,
    duplicate: bool = False,
    advertisement: bool = False,
    vpn_testing_enabled: bool = True,
) -> CollectPostUseCase:
    """Build a collection use case wired with in-memory fakes."""
    ai = AiService(
        FakeAiProvider(
            category=category, duplicate=duplicate, advertisement=advertisement
        )
    )
    return CollectPostUseCase(
        posts, queue, ai, retention_days=14, vpn_testing_enabled=vpn_testing_enabled
    )


class TestCollectPost:
    """Tests for :class:`CollectPostUseCase`."""

    async def test_stores_news_post_and_queues_approval_before_score_update(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        post = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=1, text="خبر مهم امروز")
        )
        assert post is not None
        assert post.category == PostCategory.GENERAL_NEWS
        assert posts.posts[post.post_id].text == "خبر مهم امروز"
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
        ]

    async def test_expiry_is_fourteen_days(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        before = datetime.now(timezone.utc)
        post = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=2, text="خبر")
        )
        assert post is not None
        expected = before + timedelta(days=14)
        assert post.expires_at is not None
        assert abs((post.expires_at - expected).total_seconds()) < 60

    async def test_fresh_post_quality_score_waits_until_twenty_minutes_old(
        self,
    ) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        published_at = datetime.now(timezone.utc)
        await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=21,
                text="خبر تازه",
                source_metrics=PostSourceMetrics(source_published_at=published_at),
            )
        )

        scheduled_at = queue.items[1].scheduled_at
        assert scheduled_at is not None
        assert scheduled_at >= published_at + timedelta(minutes=20)

    async def test_old_backfill_post_quality_score_is_due_immediately(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        before = datetime.now(timezone.utc)
        await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=22,
                text="خبر قدیمی امروز",
                source_metrics=PostSourceMetrics(
                    source_published_at=before - timedelta(minutes=30)
                ),
            )
        )

        scheduled_at = queue.items[1].scheduled_at
        assert scheduled_at is not None
        assert scheduled_at <= datetime.now(timezone.utc) + timedelta(seconds=2)

    async def test_stored_post_without_media_requests_and_repairs_media(self) -> None:
        """Backfill repairs media for posts that were stored text-only before."""
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        existing = Post(
            post_id="p-existing",
            source_chat_id=-100,
            source_message_id=30,
            text="خبر ویدئویی",
            content_hash=content_hash("خبر ویدئویی"),
            category=PostCategory.GENERAL_NEWS,
        )
        await posts.save(existing)

        should_download = await use_case.should_download_media(-100, 30, None, 1)
        result = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=30,
                text="خبر ویدئویی",
                media=[
                    MediaItem(
                        kind=MediaKind.VIDEO,
                        file_path="data/media/30.mp4",
                        mime_type="video/mp4",
                    )
                ],
                expected_media_count=1,
            )
        )

        assert should_download is True
        assert result is existing
        assert posts.posts["p-existing"].media[0].kind == MediaKind.VIDEO
        assert (
            posts.posts["p-existing"].media_download_status
            == MediaDownloadStatus.COMPLETE
        )

    async def test_failed_media_download_is_recorded_for_later_repair(self) -> None:
        """Text remains eligible while Mongo records an incomplete attachment."""
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)

        post = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=33,
                text="خبر ویدئویی",
                expected_media_count=1,
            )
        )

        assert post is not None
        assert post.media_download_status == MediaDownloadStatus.FAILED
        assert post.expected_media_count == 1
        assert queue.items[0].type == QueueItemType.APPROVAL_REQUEST

    async def test_stored_post_with_existing_media_does_not_request_download(
        self, tmp_path: Path
    ) -> None:
        """Existing complete media prevents needless Telegram downloads."""
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        media_path = tmp_path / "31.mp4"
        media_path.write_bytes(b"video")
        await posts.save(
            Post(
                post_id="p-existing",
                source_chat_id=-100,
                source_message_id=31,
                text="خبر ویدئویی",
                content_hash=content_hash("خبر ویدئویی"),
                media=[MediaItem(kind=MediaKind.VIDEO, file_path=str(media_path))],
            )
        )

        assert await use_case.should_download_media(-100, 31, None, 1) is False

    async def test_stored_post_with_missing_media_file_requests_download(self) -> None:
        """Missing local files are treated as repairable media gaps."""
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        await posts.save(
            Post(
                post_id="p-existing",
                source_chat_id=-100,
                source_message_id=32,
                text="خبر ویدئویی",
                content_hash=content_hash("خبر ویدئویی"),
                media=[MediaItem(kind=MediaKind.VIDEO, file_path="missing/32.mp4")],
            )
        )

        assert await use_case.should_download_media(-100, 32, None, 1) is True

    async def test_skips_empty_message(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        result = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=3, text="   ")
        )
        assert result is None
        assert not posts.posts

    async def test_exact_hash_duplicate_is_stored_as_skipped(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        first = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=4, text="خبر تکراری")
        )
        second = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-200, message_id=5, text="خبر  تکراری")
        )
        assert first is not None
        assert second is not None
        assert second.is_duplicate is True
        assert second.duplicate_of == first.post_id
        assert second.skipped_reason == "duplicate"
        assert len(posts.posts) == 2
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
        ]

    async def test_ai_detected_duplicate_is_stored_as_skipped(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, duplicate=True)
        first = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=6, text="خبر اول")
        )
        second = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=7, text="بازنویسی خبر اول")
        )
        assert first is not None
        assert second is not None
        assert second.is_duplicate is True
        assert second.skipped_reason == "duplicate"
        assert len(posts.posts) == 2
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
        ]

    async def test_local_fuzzy_duplicate_skips_ai_and_scoring(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        provider = FakeAiProvider()
        use_case = CollectPostUseCase(posts, queue, AiService(provider))
        first = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=23,
                text="خبر فوری درباره بازار ارز @source",
            )
        )
        second = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-200,
                message_id=24,
                text="خبر فوری درباره بازار ارز https://t.me/source",
            )
        )

        assert first is not None
        assert second is not None
        assert second.skipped_reason == "duplicate"
        assert second.duplicate_provider == "local_fuzzy"
        assert provider.classify_calls == 1
        assert len(queue.items) == 2

    async def test_ai_receives_only_local_duplicate_candidates(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        provider = FakeAiProvider()
        use_case = CollectPostUseCase(posts, queue, AiService(provider))
        await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=25, text="خبر اقتصاد جهان")
        )
        await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=26, text="آموزش برنامه نویسی")
        )
        await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-200, message_id=27, text="خبر اقتصاد جهان امروز")
        )

        assert provider.last_existing_texts == ["خبر اقتصاد جهان"]

    async def test_irrelevant_post_is_stored_as_skipped(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, category=PostCategory.IRRELEVANT)
        result = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=8, text="تبلیغات")
        )
        assert result is not None
        assert result.skipped_reason == "irrelevant"
        assert result.category == PostCategory.IRRELEVANT
        assert len(posts.posts) == 1
        assert not queue.items

    async def test_advertisement_post_is_pruned_before_scoring(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, advertisement=True)
        result = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=18, text="تبلیغ کانال")
        )
        assert result is not None
        assert result.skipped_reason == "advertisement"
        assert not queue.items

    async def test_vpn_config_is_not_pruned_when_ai_calls_it_advertisement(
        self,
    ) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(
            posts,
            queue,
            category=PostCategory.IRRELEVANT,
            advertisement=True,
        )
        result = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=19,
                text=f"کانفیگ تست:\n{VMESS_URI}",
            )
        )
        assert result is not None
        assert result.skipped_reason is None
        assert result.category == PostCategory.VPN_CONFIG
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
            QueueItemType.VPN_TEST,
        ]

    async def test_vpn_config_post_queues_vpn_test(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, category=PostCategory.VPN_CONFIG)
        post = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100, message_id=9, text=f"کانفیگ:\n{VMESS_URI}"
            )
        )
        assert post is not None
        assert len(post.vpn_configs) == 1
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
            QueueItemType.VPN_TEST,
        ]

    async def test_config_in_news_text_forces_vpn_config_category(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, category=PostCategory.GENERAL_NEWS)
        post = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100, message_id=10, text=f"متن\n{VMESS_URI}"
            )
        )
        assert post is not None
        assert post.category == PostCategory.VPN_CONFIG

    async def test_vpn_testing_disabled_still_approves_before_score_update(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(
            posts, queue, category=PostCategory.VPN_CONFIG, vpn_testing_enabled=False
        )
        await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=11, text=VMESS_URI)
        )
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
        ]

    async def test_ai_classification_failure_still_stores_for_manual_approval(
        self,
    ) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        ai = AiService(
            FakeAiProvider(name="zai", fail=True),
            FakeAiProvider(name="deepseek", fail=True),
        )
        use_case = CollectPostUseCase(posts, queue, ai, retention_days=14)
        post = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=12, text="متن خبر")
        )
        assert post is not None
        assert post.category == PostCategory.GENERAL_NEWS
        assert post.ai_provider == "unavailable"
        assert posts.posts[post.post_id].text == "متن خبر"
        assert queue.items[0].type == QueueItemType.APPROVAL_REQUEST

    async def test_stored_source_without_pipeline_is_requeued(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        existing = Post(
            post_id="stored",
            source_chat_id=-100,
            source_message_id=15,
            text="خبر ذخیره شده اما ارسال نشده",
            content_hash=content_hash("خبر ذخیره شده اما ارسال نشده"),
        )
        await posts.save(existing)
        use_case = _use_case(posts, queue)

        result = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100,
                message_id=15,
                text=existing.text,
            )
        )

        assert result is existing
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.SOURCE_METRICS_REFRESH,
        ]
        assert queue.items[0].payload == {"post_id": "stored"}

    async def test_stored_source_with_legacy_score_is_repaired_to_approval(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        existing = Post(
            post_id="stored",
            source_chat_id=-100,
            source_message_id=16,
            text="خبر قبلی",
            content_hash=content_hash("خبر قبلی"),
        )
        await posts.save(existing)
        await queue.enqueue(QueueItemType.QUALITY_SCORE, {"post_id": "stored"})
        use_case = _use_case(posts, queue)

        result = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=16, text=existing.text)
        )

        assert result is existing
        assert [item.type for item in queue.items] == [
            QueueItemType.QUALITY_SCORE,
            QueueItemType.APPROVAL_REQUEST,
        ]

    async def test_has_seen_source_message_uses_grouped_identity(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        existing = Post(
            post_id="album",
            source_chat_id=-100,
            source_message_id=20,
            grouped_id=555,
            text="آلبوم",
            content_hash=content_hash("آلبوم"),
        )
        await posts.save(existing)
        use_case = _use_case(posts, queue)

        assert await use_case.has_seen_source_message(-100, 20, 555) is True
        assert await use_case.has_seen_source_message(-100, 20, 556) is False

    async def test_dialog_vpn_discovery_is_immediate_and_not_scored(self) -> None:
        """Discovery posts enqueue approval immediately and skip quality scoring."""
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)

        post = await use_case.handle_vpn_discovery_message(
            CollectedMessage(
                source_chat_id=-300,
                message_id=1,
                source_label="VPN Group",
                text=f"تبلیغ کانال\n{VMESS_URI}",
            )
        )

        assert post is not None
        assert post.ingestion_mode == IngestionMode.DIALOG_VPN_DISCOVERY
        assert post.quality_score_status == QualityScoreStatus.NOT_REQUIRED
        assert [item.type for item in queue.items] == [
            QueueItemType.APPROVAL_REQUEST,
            QueueItemType.VPN_TEST,
        ]

    async def test_dialog_vpn_discovery_deduplicates_config_across_chats(self) -> None:
        """A repeated URI is stored as skipped and never proposed twice."""
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        first = await use_case.handle_vpn_discovery_message(
            CollectedMessage(source_chat_id=-300, message_id=1, text=VMESS_URI)
        )
        second = await use_case.handle_vpn_discovery_message(
            CollectedMessage(source_chat_id=-400, message_id=2, text=VMESS_URI)
        )

        assert first is not None
        assert second is not None
        assert second.skipped_reason == "duplicate_vpn_configs"
        assert len(queue.items) == 2
