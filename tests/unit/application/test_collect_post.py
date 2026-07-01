"""Unit tests for the post collection pipeline."""

from __future__ import annotations

import base64
import json
from datetime import datetime, timedelta, timezone

from src.application.ai_service import AiService
from src.application.collect_post import CollectedMessage, CollectPostUseCase
from src.domain.enums import PostCategory, QueueItemType
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
    vpn_testing_enabled: bool = True,
) -> CollectPostUseCase:
    """Build a use case wired with fakes."""
    ai = AiService(FakeAiProvider(category=category, duplicate=duplicate))
    return CollectPostUseCase(
        posts, queue, ai, retention_days=14, vpn_testing_enabled=vpn_testing_enabled
    )


class TestCollectPost:
    """Tests for :class:`CollectPostUseCase`."""

    async def test_stores_news_post_and_queues_approval(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        post = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=1, text="خبر مهم امروز")
        )
        assert post is not None
        assert post.category == PostCategory.GENERAL_NEWS
        assert posts.posts[post.post_id].text == "خبر مهم امروز"
        assert len(queue.items) == 1
        assert queue.items[0].type == QueueItemType.APPROVAL_REQUEST

    async def test_expiry_is_fourteen_days(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        before = datetime.now(timezone.utc)
        post = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=2, text="خبر")
        )
        expected = before + timedelta(days=14)
        assert post.expires_at is not None
        assert abs((post.expires_at - expected).total_seconds()) < 60

    async def test_skips_empty_message(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        result = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=3, text="   ")
        )
        assert result is None
        assert not posts.posts

    async def test_skips_exact_hash_duplicate(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue)
        first = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=4, text="خبر تکراری")
        )
        second = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-200, message_id=5, text="خبر  تکراری")
        )
        assert first is not None
        assert second is None
        assert len(posts.posts) == 1

    async def test_skips_ai_detected_duplicate(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, duplicate=True)
        await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=6, text="خبر اول")
        )
        second = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=7, text="بازنویسی خبر اول")
        )
        assert second is None
        assert len(posts.posts) == 1

    async def test_skips_irrelevant_post(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, category=PostCategory.IRRELEVANT)
        result = await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=8, text="تبلیغات")
        )
        assert result is None
        assert not posts.posts
        assert not queue.items

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
        assert queue.items[0].type == QueueItemType.VPN_TEST

    async def test_config_in_news_text_forces_vpn_config_category(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(posts, queue, category=PostCategory.GENERAL_NEWS)
        post = await use_case.handle_new_message(
            CollectedMessage(
                source_chat_id=-100, message_id=10, text=f"متن\n{VMESS_URI}"
            )
        )
        assert post.category == PostCategory.VPN_CONFIG

    async def test_vpn_testing_disabled_goes_straight_to_approval(self) -> None:
        posts, queue = FakePostRepository(), FakeQueueRepository()
        use_case = _use_case(
            posts, queue, category=PostCategory.VPN_CONFIG, vpn_testing_enabled=False
        )
        await use_case.handle_new_message(
            CollectedMessage(source_chat_id=-100, message_id=11, text=VMESS_URI)
        )
        assert queue.items[0].type == QueueItemType.APPROVAL_REQUEST
