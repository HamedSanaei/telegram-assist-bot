"""In-memory fakes implementing the domain protocols for unit tests."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from src.domain.entities import (
    AdminUser,
    ApprovalMessageRef,
    ApprovalPreviewRefreshResult,
    DestinationChannel,
    DollarPrice,
    Post,
    PublishLogEntry,
    QueueItem,
    VpnConfig,
)
from src.domain.enums import PostCategory, QueueItemType, QueueStatus
from src.domain.interfaces import (
    AiClassificationResult,
    AiPostAnalysisResult,
    AiTextCleanupResult,
    DuplicateCheckResult,
    QualityScoreResult,
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
        score: float = 7.0,
        advertisement: bool = False,
        fail: bool = False,
        fail_message: str | None = None,
    ) -> None:
        self.name = name
        self._category = category
        self._duplicate = duplicate
        self._score = score
        self._advertisement = advertisement
        self._fail = fail
        self._fail_message = fail_message
        self.classify_calls = 0
        self.duplicate_calls = 0
        self.score_calls = 0
        self.cleanup_calls = 0
        self.last_existing_texts: list[str] = []

    async def classify_post(self, text: str) -> AiClassificationResult:
        self.classify_calls += 1
        if self._fail:
            raise AiProviderError(self._fail_message or f"{self.name} is down")
        return AiClassificationResult(category=self._category, provider=self.name)

    async def is_duplicate(
        self, new_text: str, existing_texts: list[str]
    ) -> DuplicateCheckResult:
        self.duplicate_calls += 1
        self.last_existing_texts = list(existing_texts)
        if self._fail:
            raise AiProviderError(self._fail_message or f"{self.name} is down")
        return DuplicateCheckResult(is_duplicate=self._duplicate, provider=self.name)

    async def analyze_post(
        self, new_text: str, existing_texts: list[str]
    ) -> AiPostAnalysisResult:
        """Return scripted duplicate and classification data in one call."""
        self.duplicate_calls += 1
        self.classify_calls += 1
        self.last_existing_texts = list(existing_texts)
        if self._fail:
            raise AiProviderError(self._fail_message or f"{self.name} is down")
        return AiPostAnalysisResult(
            category=self._category,
            is_duplicate=self._duplicate if existing_texts else False,
            provider=self.name,
            is_advertisement=self._advertisement,
            reason="تحلیل آزمایشی",
        )

    async def score_post(
        self,
        text: str,
        category: PostCategory | None,
        metrics: dict[str, object],
    ) -> QualityScoreResult:
        self.score_calls += 1
        if self._fail:
            raise AiProviderError(self._fail_message or f"{self.name} is down")
        return QualityScoreResult(
            score=self._score,
            reason="امتیاز آزمایشی",
            provider=self.name,
            raw_metrics=dict(metrics),
        )

    async def clean_vpn_post(self, text: str) -> AiTextCleanupResult:
        """Return protected text unchanged for discovery tests."""
        self.cleanup_calls += 1
        if self._fail:
            raise AiProviderError(self._fail_message or f"{self.name} is down")
        return AiTextCleanupResult(text=text, provider=self.name)


class FakePostRepository:
    """Dict-backed post repository."""

    def __init__(self) -> None:
        self.posts: dict[str, Post] = {}

    async def save(self, post: Post) -> None:
        self.posts[post.post_id] = post

    async def insert_if_absent(self, post: Post) -> bool:
        """Insert unless another post already owns the source identity."""
        for existing in self.posts.values():
            if (
                existing.source_chat_id == post.source_chat_id
                and existing.source_message_id == post.source_message_id
                and existing.grouped_id == post.grouped_id
            ):
                return False
        self.posts[post.post_id] = post
        return True

    async def get(self, post_id: str) -> Post | None:
        return self.posts.get(post_id)

    async def find_by_content_hash(self, content_hash: str) -> Post | None:
        fallback: Post | None = None
        for post in self.posts.values():
            if post.content_hash == content_hash:
                if not post.is_duplicate and not post.skipped_reason:
                    return post
                fallback = fallback or post
        return fallback

    async def find_by_source_message(
        self, source_chat_id: int, source_message_id: int, grouped_id: int | None = None
    ) -> Post | None:
        for post in self.posts.values():
            if (
                post.source_chat_id == source_chat_id
                and post.source_message_id == source_message_id
                and post.grouped_id == grouped_id
            ):
                return post
        return None

    async def list_recent_texts(self, limit: int) -> list[str]:
        texts = [
            p.text
            for p in self.posts.values()
            if p.text and not p.is_duplicate and not p.skipped_reason
        ]
        return texts[-limit:]

    async def find_seen_vpn_fingerprints(self, fingerprints: list[str]) -> set[str]:
        """Return requested fingerprints already stored by fake posts."""
        known = {
            fingerprint
            for post in self.posts.values()
            for fingerprint in post.vpn_fingerprints
        }
        return known.intersection(fingerprints)

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

    async def enqueue_if_missing_post_item(
        self,
        item_type: QueueItemType,
        post_id: str,
        payload: dict[str, object],
        scheduled_at: datetime | None = None,
    ) -> int | None:
        """Enqueue only when the fake has no active/successful matching item."""
        if await self.has_active_or_successful_post_item(post_id, {item_type}):
            return None
        return await self.enqueue(item_type, payload, scheduled_at)

    async def claim_next_due(
        self, now: datetime, allowed_types: set[QueueItemType] | None = None
    ) -> QueueItem | None:
        for item in self.items:
            if (
                item.status == QueueStatus.PENDING
                and item.scheduled_at <= now
                and (not allowed_types or item.type in allowed_types)
            ):
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

    async def has_active_or_successful_post_item(
        self, post_id: str, item_types: set[QueueItemType]
    ) -> bool:
        active_statuses = {
            QueueStatus.PENDING,
            QueueStatus.PROCESSING,
            QueueStatus.WAITING_APPROVAL,
            QueueStatus.APPROVED,
            QueueStatus.COMPLETED,
            QueueStatus.PUBLISHED,
        }
        return any(
            item.payload.get("post_id") == post_id
            and item.type in item_types
            and item.status in active_statuses
            for item in self.items
        )

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


class FakeApprovalRequestRepository:
    """In-memory approval request idempotency repository."""

    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}
        self.errors: dict[str, str] = {}

    async def has_requested(self, post_id: str) -> bool:
        """Return whether the post id has been recorded."""
        return self.statuses.get(post_id) in {"reserved", "sent"}

    async def record_requested(self, post_id: str) -> None:
        """Record one sent approval request."""
        self.statuses[post_id] = "sent"
        self.errors.pop(post_id, None)

    async def reserve_request(self, post_id: str) -> bool:
        """Reserve one approval request unless it is already active."""
        status = self.statuses.get(post_id)
        if status in {"reserved", "sent"}:
            return False
        self.statuses[post_id] = "reserved"
        self.errors.pop(post_id, None)
        return True

    async def mark_sent(self, post_id: str) -> None:
        """Mark one approval request as sent."""
        self.statuses[post_id] = "sent"
        self.errors.pop(post_id, None)

    async def mark_failed(self, post_id: str, error: str) -> None:
        """Mark one approval request as failed."""
        self.statuses[post_id] = "failed"
        self.errors[post_id] = error

    async def list_requested_post_ids(self) -> list[str]:
        """Return requested post ids in deterministic order."""
        return sorted(
            post_id
            for post_id, status in self.statuses.items()
            if status in {"reserved", "sent"}
        )


class FakeApprovalMessageRepository:
    """In-memory approval message reference repository."""

    def __init__(self) -> None:
        self.refs: list[ApprovalMessageRef] = []
        self.deactivated: set[int] = set()

    async def record_messages(self, refs: list[ApprovalMessageRef]) -> None:
        """Record delivered approval message refs."""
        for ref in refs:
            ref.id = len(self.refs) + 1
            self.refs.append(ref)

    async def list_active(self, post_id: str) -> list[ApprovalMessageRef]:
        """Return active refs for a post."""
        return [
            ref
            for ref in self.refs
            if ref.post_id == post_id and ref.active and ref.id not in self.deactivated
        ]

    async def set_delivery_mode(
        self, post_id: str, chat_id: int, message_id: int, delivery_mode: str
    ) -> None:
        """Update one ref's delivery mode."""
        for ref in self.refs:
            if (
                ref.post_id == post_id
                and ref.chat_id == chat_id
                and ref.message_id == message_id
            ):
                ref.delivery_mode = delivery_mode

    async def deactivate(self, message_ref_id: int) -> None:
        """Deactivate one ref by id."""
        self.deactivated.add(message_ref_id)

    async def activate(self, message_ref_id: int) -> None:
        """Reactivate one ref by id."""
        self.deactivated.discard(message_ref_id)

    async def list_recent_inactive(
        self, updated_since: datetime, limit: int = 500
    ) -> list[ApprovalMessageRef]:
        """Return inactive refs; timestamps are irrelevant for this fake."""
        del updated_since
        return [
            ref
            for ref in self.refs
            if ref.id in self.deactivated or not ref.active
        ][:limit]

    async def list_active_post_ids(self) -> list[str]:
        """Return post ids with active refs."""
        return sorted({ref.post_id for ref in await self.list_active_refs()})

    async def deactivate_admins_except(self, admin_user_ids: set[int]) -> int:
        """Deactivate refs belonging to removed admins."""
        count = 0
        for ref in self.refs:
            if ref.admin_user_id not in admin_user_ids and ref.id is not None:
                self.deactivated.add(ref.id)
                count += 1
        return count

    async def list_active_refs(self) -> list[ApprovalMessageRef]:
        """Return all active refs."""
        return [
            ref
            for ref in self.refs
            if ref.active and ref.id not in self.deactivated
        ]


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

    async def disable_sources_except(self, identifiers: set[str]) -> int:
        return 0

    async def disable_destinations_except(self, chat_ids: set[int]) -> int:
        before = len([channel for channel in self.destinations if channel.enabled])
        for channel in self.destinations:
            if channel.chat_id not in chat_ids:
                channel.enabled = False
        after = len([channel for channel in self.destinations if channel.enabled])
        return before - after


class FakeAdminRepository:
    """Set-backed admin repository."""

    def __init__(self, admin_ids: set[int] | None = None) -> None:
        self.admin_ids = admin_ids or set()

    async def upsert(self, admin: AdminUser) -> None:
        self.admin_ids.add(admin.telegram_user_id)

    async def replace_all(self, admins: list[AdminUser]) -> None:
        self.admin_ids = {admin.telegram_user_id for admin in admins}

    async def is_admin(self, telegram_user_id: int) -> bool:
        return telegram_user_id in self.admin_ids

    async def list_user_ids(self) -> list[int]:
        return sorted(self.admin_ids)


class FakePublishLogRepository:
    """Dict-backed publish log."""

    def __init__(self) -> None:
        self.records: dict[tuple[str, int], PublishLogEntry] = {}
        self.published_times: dict[int, datetime] = {}

    async def has_any_delivery_record(self, post_id: str) -> bool:
        """Return whether the fake has any publish-log row for the post."""
        return any(pid == post_id for pid, _ in self.records)

    async def is_published(self, post_id: str, channel_chat_id: int) -> bool:
        record = self.records.get((post_id, channel_chat_id))
        return (
            record is not None
            and record.mode == "immediate"
            and record.status in {"reserved", "published"}
        )

    async def record_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        self.records[(post_id, channel_chat_id)] = PublishLogEntry(
            post_id=post_id,
            channel_chat_id=channel_chat_id,
            mode="immediate",
            status="published",
            message_id=message_id,
            published_at=datetime.now(timezone.utc),
        )
        self.published_times[channel_chat_id] = datetime.now(timezone.utc)

    async def try_reserve_publish(
        self, post_id: str, channel_chat_id: int, mode: str
    ) -> bool:
        key = (post_id, channel_chat_id)
        if key in self.records and self.records[key].status != "removed":
            return False
        self.records[key] = PublishLogEntry(
            post_id=post_id,
            channel_chat_id=channel_chat_id,
            mode=mode,
            status="reserved",
            published_at=datetime.now(timezone.utc),
        )
        return True

    async def mark_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        self.records[(post_id, channel_chat_id)] = PublishLogEntry(
            post_id=post_id,
            channel_chat_id=channel_chat_id,
            mode="immediate",
            status="published",
            message_id=message_id,
            published_at=datetime.now(timezone.utc),
        )
        self.published_times[channel_chat_id] = datetime.now(timezone.utc)

    async def mark_scheduled(
        self,
        post_id: str,
        channel_chat_id: int,
        message_id: int,
        scheduled_at: datetime,
    ) -> None:
        self.records[(post_id, channel_chat_id)] = PublishLogEntry(
            post_id=post_id,
            channel_chat_id=channel_chat_id,
            mode="scheduled",
            status="scheduled",
            message_id=message_id,
            published_at=datetime.now(timezone.utc),
            scheduled_at=scheduled_at,
        )

    async def release_reservation(self, post_id: str, channel_chat_id: int) -> None:
        key = (post_id, channel_chat_id)
        if key in self.records and self.records[key].status == "reserved":
            del self.records[key]

    async def published_channels(self, post_id: str) -> set[int]:
        return {
            chat
            for (pid, chat), record in self.records.items()
            if pid == post_id
            and record.mode == "immediate"
            and record.status in {"reserved", "published"}
        }

    async def scheduled_channels(self, post_id: str) -> set[int]:
        return {
            chat
            for (pid, chat), record in self.records.items()
            if pid == post_id
            and record.mode == "scheduled"
            and record.status in {"reserved", "scheduled"}
        }

    async def get_active_record(
        self, post_id: str, channel_chat_id: int
    ) -> PublishLogEntry | None:
        record = self.records.get((post_id, channel_chat_id))
        if record is None or record.status == "removed":
            return None
        return record

    async def mark_removed(self, post_id: str, channel_chat_id: int) -> None:
        record = self.records[(post_id, channel_chat_id)]
        self.records[(post_id, channel_chat_id)] = PublishLogEntry(
            post_id=record.post_id,
            channel_chat_id=record.channel_chat_id,
            mode=record.mode,
            status="removed",
            published_at=record.published_at,
            scheduled_at=record.scheduled_at,
            removed_at=datetime.now(timezone.utc),
        )

    async def last_published_at(self, channel_chat_id: int) -> datetime | None:
        return self.published_times.get(channel_chat_id)

    async def list_history(self, post_id: str) -> list[PublishLogEntry]:
        """Return all fake publish rows for a post."""
        return [
            record
            for (pid, _), record in self.records.items()
            if pid == post_id
        ]


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
        self.post_entities: list[tuple[int, list[object]]] = []
        self.deleted: list[tuple[int, int]] = []
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
        self.post_entities.append((chat_id, list(post.text_entities)))
        self._next_message_id += 1
        return self._next_message_id

    async def delete_message(self, chat_id: int, message_id: int) -> None:
        if self._fail:
            raise TelegramPublishError("delete failed")
        self.deleted.append((chat_id, message_id))


class FakeScheduledPublisher:
    """Records native Telegram scheduled posts for approval tests."""

    def __init__(self, latest: datetime | None = None) -> None:
        self.latest = latest
        self.scheduled: list[tuple[int, str, datetime]] = []
        self.deleted: list[tuple[int, int]] = []
        self._next_message_id = 500

    async def latest_scheduled_at(self, chat_id: int) -> datetime | None:
        """Return the scripted latest schedule time."""
        return self.latest

    async def schedule_post(
        self, chat_id: int, post: Post, scheduled_at: datetime
    ) -> int:
        """Record one scheduled post and return a fake message id."""
        self.scheduled.append((chat_id, post.post_id, scheduled_at))
        self.latest = scheduled_at
        self._next_message_id += 1
        return self._next_message_id

    async def delete_scheduled_message(self, chat_id: int, message_id: int) -> None:
        """Record one scheduled message deletion."""
        self.deleted.append((chat_id, message_id))


class FakeMetadataRefresher:
    """Returns scripted source metrics for quality-score tests."""

    def __init__(self, metrics: object | None = None) -> None:
        self.metrics = metrics
        self.calls: list[tuple[int, int]] = []

    async def refresh_metrics(self, source_chat_id: int, source_message_id: int) -> object | None:
        """Record one refresh attempt and return the scripted metrics."""
        self.calls.append((source_chat_id, source_message_id))
        return self.metrics


class FakeApprovalNotifier:
    """Records approval previews sent by the approval service."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, list[int]]] = []
        self.refreshed: list[tuple[str, list[int]]] = []

    async def send_approval_request(
        self, post: Post, channels: list[DestinationChannel]
    ) -> list[ApprovalMessageRef]:
        """Record the requested post id and destination channel ids."""
        self.sent.append((post.post_id, [channel.chat_id for channel in channels]))
        return [
            ApprovalMessageRef(
                post_id=post.post_id,
                admin_user_id=1,
                chat_id=1,
                message_id=len(self.sent),
            )
        ]

    async def refresh_approval_request(
        self,
        post: Post,
        channels: list[DestinationChannel],
        published_chat_ids: set[int],
        scheduled_chat_ids: set[int],
        has_delivery_history: bool,
        refs: list[ApprovalMessageRef] | None = None,
    ) -> ApprovalPreviewRefreshResult:
        """Record one in-place preview refresh."""
        del published_chat_ids, scheduled_chat_ids, has_delivery_history, refs
        self.refreshed.append(
            (post.post_id, [channel.chat_id for channel in channels])
        )
        return ApprovalPreviewRefreshResult(updated=1)


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
