"""In-memory fakes implementing the domain protocols for unit tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.domain.entities import (
    AdminUser,
    DestinationChannel,
    DollarPrice,
    Post,
    QueueItem,
    VpnConfig,
)
from src.domain.enums import PostCategory, QueueItemType, QueueStatus
from src.domain.interfaces import (
    AiClassificationResult,
    DuplicateCheckResult,
    VpnTestResult,
)
from src.shared.errors import AiProviderError, TelegramPublishError


class FakeAiProvider:
    """Scripted AI provider; can be told to fail to exercise fallback."""

    def __init__(
        self,
        name: str = "fake",
        category: PostCategory = PostCategory.GENERAL_NEWS,
        duplicate: bool = False,
        fail: bool = False,
    ) -> None:
        self.name = name
        self._category = category
        self._duplicate = duplicate
        self._fail = fail
        self.classify_calls = 0
        self.duplicate_calls = 0

    async def classify_post(self, text: str) -> AiClassificationResult:
        self.classify_calls += 1
        if self._fail:
            raise AiProviderError(f"{self.name} is down")
        return AiClassificationResult(category=self._category, provider=self.name)

    async def is_duplicate(
        self, new_text: str, existing_texts: list[str]
    ) -> DuplicateCheckResult:
        self.duplicate_calls += 1
        if self._fail:
            raise AiProviderError(f"{self.name} is down")
        return DuplicateCheckResult(is_duplicate=self._duplicate, provider=self.name)


class FakePostRepository:
    """Dict-backed post repository."""

    def __init__(self) -> None:
        self.posts: dict[str, Post] = {}

    async def save(self, post: Post) -> None:
        self.posts[post.post_id] = post

    async def get(self, post_id: str) -> Post | None:
        return self.posts.get(post_id)

    async def find_by_content_hash(self, content_hash: str) -> Post | None:
        for post in self.posts.values():
            if post.content_hash == content_hash:
                return post
        return None

    async def list_recent_texts(self, limit: int) -> list[str]:
        texts = [p.text for p in self.posts.values() if p.text]
        return texts[-limit:]

    async def update_vpn_configs(self, post_id: str, configs: list[VpnConfig]) -> None:
        self.posts[post_id].vpn_configs = configs

    async def delete_expired(self, now: datetime) -> int:
        expired = [
            pid
            for pid, post in self.posts.items()
            if post.expires_at is not None and post.expires_at <= now
        ]
        for pid in expired:
            del self.posts[pid]
        return len(expired)


class FakeQueueRepository:
    """List-backed queue repository (enqueue/inspect only)."""

    def __init__(self) -> None:
        self.items: list[QueueItem] = []

    async def enqueue(
        self,
        item_type: QueueItemType,
        payload: dict[str, object],
        scheduled_at: datetime | None = None,
    ) -> int:
        item = QueueItem(
            id=len(self.items) + 1,
            type=item_type,
            payload=payload,
            scheduled_at=scheduled_at or datetime.now(timezone.utc),
        )
        self.items.append(item)
        return item.id

    async def claim_next_due(self, now: datetime) -> QueueItem | None:
        for item in self.items:
            if item.status == QueueStatus.PENDING and item.scheduled_at <= now:
                item.status = QueueStatus.PROCESSING
                item.attempts += 1
                return item
        return None

    async def mark_status(
        self, item_id: int, status: QueueStatus, last_error: str | None = None
    ) -> None:
        for item in self.items:
            if item.id == item_id:
                item.status = status
                item.last_error = last_error

    async def reschedule(
        self, item_id: int, scheduled_at: datetime, last_error: str
    ) -> None:
        for item in self.items:
            if item.id == item_id:
                item.status = QueueStatus.PENDING
                item.scheduled_at = scheduled_at
                item.last_error = last_error

    async def expire_older_than(self, cutoff: datetime) -> int:
        count = 0
        for item in self.items:
            created = item.created_at or item.scheduled_at
            if item.status == QueueStatus.PENDING and created and created < cutoff:
                item.status = QueueStatus.EXPIRED
                count += 1
        return count

    def _pending_scheduled(self) -> list[QueueItem]:
        return [
            item
            for item in self.items
            if item.type == QueueItemType.SCHEDULED_PUBLISH
            and item.status in (QueueStatus.PENDING, QueueStatus.PROCESSING)
        ]

    async def latest_scheduled_publish_for_channel(
        self, channel_chat_id: int
    ) -> datetime | None:
        times = [
            item.scheduled_at
            for item in self._pending_scheduled()
            if item.payload.get("chat_id") == channel_chat_id and item.scheduled_at
        ]
        return max(times) if times else None

    async def scheduled_publish_channels(self, post_id: str) -> set[int]:
        return {
            int(item.payload["chat_id"])
            for item in self._pending_scheduled()
            if item.payload.get("post_id") == post_id
        }


class FakeChannelRepository:
    """Static channel repository."""

    def __init__(
        self,
        destinations: list[DestinationChannel] | None = None,
        source_usernames: list[str] | None = None,
    ) -> None:
        self.destinations = destinations or []
        self.source_usernames = source_usernames or []

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        self.destinations = [
            c for c in self.destinations if c.chat_id != channel.chat_id
        ]
        self.destinations.append(channel)

    async def list_destinations(self) -> list[DestinationChannel]:
        return [c for c in self.destinations if c.enabled]

    async def get_destination(self, chat_id: int) -> DestinationChannel | None:
        return next((c for c in self.destinations if c.chat_id == chat_id), None)

    async def list_price_channels(self) -> list[DestinationChannel]:
        return [c for c in self.destinations if c.enabled and c.publish_usd_price]

    async def upsert_source(self, identifier: str) -> None:
        pass

    async def upsert_source_details(
        self,
        identifier: str,
        chat_id: int,
        title: str,
        username: str,
    ) -> None:
        pass

    async def get_source_label(self, chat_id: int) -> str | None:
        return None

    async def list_sources(self) -> list[str]:
        return []

    async def list_source_usernames(self) -> list[str]:
        return list(self.source_usernames)

    async def disable_source(self, identifier: str) -> bool:
        return False


class FakeAdminRepository:
    """Set-backed admin repository."""

    def __init__(self, admin_ids: set[int] | None = None) -> None:
        self.admin_ids = admin_ids or set()

    async def upsert(self, admin: AdminUser) -> None:
        self.admin_ids.add(admin.telegram_user_id)

    async def is_admin(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self.admin_ids

    async def list_user_ids(self) -> list[int]:
        return sorted(self.admin_ids)


class FakePublishLogRepository:
    """Dict-backed publish log."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, int], int] = {}
        self.published_times: dict[int, datetime] = {}

    async def is_published(self, post_id: str, channel_chat_id: int) -> bool:
        return (post_id, channel_chat_id) in self.records

    async def record_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        self.records[(post_id, channel_chat_id)] = message_id
        self.published_times[channel_chat_id] = datetime.now(timezone.utc)

    async def published_channels(self, post_id: str) -> set[int]:
        return {chat for (pid, chat) in self.records if pid == post_id}

    async def last_published_at(self, channel_chat_id: int) -> datetime | None:
        return self.published_times.get(channel_chat_id)


class FakePriceHistoryRepository:
    """List-backed price history."""

    def __init__(self) -> None:
        self.prices: list[DollarPrice] = []

    async def save(self, price: DollarPrice) -> int:
        self.prices.append(price)
        return len(self.prices)

    async def get_latest(self) -> DollarPrice | None:
        return self.prices[-1] if self.prices else None


class FakePublisher:
    """Records published messages; can be told to fail."""

    def __init__(self, fail: bool = False) -> None:
        self.texts: list[tuple[int, str]] = []
        self.posts: list[tuple[int, str]] = []
        self.post_texts: list[tuple[int, str]] = []
        self._fail = fail
        self._next_message_id = 100

    async def publish_text(self, chat_id: int, text: str) -> int:
        if self._fail:
            raise TelegramPublishError("send failed")
        self.texts.append((chat_id, text))
        self._next_message_id += 1
        return self._next_message_id

    async def publish_post(self, chat_id: int, post: Post) -> int:
        if self._fail:
            raise TelegramPublishError("send failed")
        self.posts.append((chat_id, post.post_id))
        self.post_texts.append((chat_id, post.text))
        self._next_message_id += 1
        return self._next_message_id


class FakeVpnTester:
    """Scripted VPN tester mapping host -> working flag."""

    def __init__(self, working_hosts: set[str] | None = None) -> None:
        self.working_hosts = working_hosts or set()
        self.tested: list[str] = []

    async def test(self, config: VpnConfig) -> VpnTestResult:
        self.tested.append(config.host)
        working = config.host in self.working_hosts
        return VpnTestResult(working=working, latency_ms=42 if working else None)


class FakePriceSource:
    """Returns a scripted sequence of prices."""

    def __init__(self, prices: list[Decimal]) -> None:
        self.name = "fake-source"
        self._prices = list(prices)

    async def fetch_price(self) -> DollarPrice:
        return DollarPrice(
            price=self._prices.pop(0),
            source=self.name,
            fetched_at=datetime.now(timezone.utc),
        )
