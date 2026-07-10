"""Unit tests for the SQLite repositories and migrations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.domain.entities import (
    AdminUser,
    ApprovalMessageRef,
    DestinationChannel,
    DollarPrice,
    RecurringForwardOccurrence,
)
from src.domain.enums import ChannelKind, QueueItemType, QueueStatus
from src.infrastructure.db.sqlite.connection import Database
from src.infrastructure.db.sqlite.migrations import apply_migrations
from src.infrastructure.db.sqlite.repositories import (
    SqliteAdminRepository,
    SqliteApprovalMessageRepository,
    SqliteApprovalRequestRepository,
    SqliteChannelRepository,
    SqlitePriceHistoryRepository,
    SqlitePublishLogRepository,
    SqliteQueueRepository,
    SqliteRecurringForwardCampaignRepository,
    SqliteRecurringForwardOccurrenceRepository,
)
from src.shared.config import RecurringForwardConfig


@pytest.fixture
async def db(tmp_path):
    """Provide a connected, migrated database in a temp directory."""
    database = Database(tmp_path / "test.db")
    await database.connect()
    await apply_migrations(database)
    yield database
    await database.close()


class TestMigrations:
    """Tests for the migration runner."""

    async def test_migrations_are_repeatable(self, db: Database) -> None:
        assert await apply_migrations(db) == 0

    async def test_existing_column_without_marker_is_repaired(
        self, tmp_path
    ) -> None:
        database = Database(tmp_path / "partial.db")
        await database.connect()
        await database.executescript(
            """
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL
            );
            INSERT INTO schema_migrations (version, applied_at)
                VALUES (1, 'now'), (2, 'now'), (3, 'now');

            CREATE TABLE destination_channels (
                chat_id INTEGER PRIMARY KEY,
                title TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'news',
                publish_usd_price INTEGER NOT NULL DEFAULT 0,
                enabled INTEGER NOT NULL DEFAULT 1,
                public_id TEXT NOT NULL DEFAULT '',
                post_interval_minutes INTEGER NOT NULL DEFAULT 30
            );

            CREATE TABLE source_channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                identifier TEXT NOT NULL UNIQUE,
                enabled INTEGER NOT NULL DEFAULT 1,
                chat_id INTEGER,
                title TEXT NOT NULL DEFAULT '',
                username TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE admins (
                telegram_user_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE queue_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                payload TEXT NOT NULL DEFAULT '{}',
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                scheduled_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE publish_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id TEXT NOT NULL,
                channel_chat_id INTEGER NOT NULL,
                message_id INTEGER,
                published_at TEXT NOT NULL,
                UNIQUE (post_id, channel_chat_id)
            );

            CREATE TABLE price_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                price TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT '',
                fetched_at TEXT NOT NULL
            );
            """
        )

        applied = await apply_migrations(database)

        assert applied == 9
        row = await database.fetchone(
            "SELECT 1 FROM schema_migrations WHERE version = 4"
        )
        assert row is not None
        await database.close()

    async def test_legacy_preview_rows_are_marked_unknown(self, db: Database) -> None:
        """Migration 12 must not relabel previews created after migration 10."""
        applied = await db.fetchone(
            "SELECT applied_at FROM schema_migrations WHERE version = 10"
        )
        assert applied is not None
        await db.execute("DELETE FROM schema_migrations WHERE version = 12")
        await db.execute(
            """
            INSERT INTO approval_messages
                (post_id, admin_user_id, chat_id, message_id, delivery_mode,
                 preview_kind, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 's', 'text', 1, ?, ?)
            """,
            ("legacy", 1, 1, 10, "2000-01-01T00:00:00+00:00", "2000-01-01T00:00:00+00:00"),
        )
        await db.execute(
            """
            INSERT INTO approval_messages
                (post_id, admin_user_id, chat_id, message_id, delivery_mode,
                 preview_kind, active, created_at, updated_at)
            VALUES (?, ?, ?, ?, 's', 'text', 1, ?, ?)
            """,
            ("current", 1, 1, 11, "2999-01-01T00:00:00+00:00", "2999-01-01T00:00:00+00:00"),
        )

        assert await apply_migrations(db) == 1
        rows = await db.fetchall(
            "SELECT post_id, preview_kind FROM approval_messages ORDER BY post_id"
        )

        assert [(row["post_id"], row["preview_kind"]) for row in rows] == [
            ("current", "text"),
            ("legacy", "unknown"),
        ]


class TestQueueRepository:
    """Tests for :class:`SqliteQueueRepository`."""

    async def test_enqueue_and_claim(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        item_id = await repo.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})
        item = await repo.claim_next_due(datetime.now(timezone.utc))
        assert item is not None
        assert item.id == item_id
        assert item.status == QueueStatus.PROCESSING
        assert item.attempts == 1
        assert item.payload == {"post_id": "p1"}

    async def test_claimed_item_not_claimed_twice(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        await repo.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})
        now = datetime.now(timezone.utc)
        assert await repo.claim_next_due(now) is not None
        assert await repo.claim_next_due(now) is None

    async def test_claim_can_be_restricted_to_worker_job_types(
        self, db: Database
    ) -> None:
        """Main and collector workers cannot steal each other's jobs."""
        repo = SqliteQueueRepository(db)
        await repo.enqueue(QueueItemType.SOURCE_METRICS_REFRESH, {"post_id": "p1"})
        await repo.enqueue(QueueItemType.APPROVAL_REQUEST, {"post_id": "p1"})

        item = await repo.claim_next_due(
            datetime.now(timezone.utc), {QueueItemType.APPROVAL_REQUEST}
        )

        assert item is not None
        assert item.type == QueueItemType.APPROVAL_REQUEST

    async def test_post_pipeline_enqueue_is_idempotent(self, db: Database) -> None:
        """Concurrent ingestion stages cannot create duplicate queue rows."""
        repo = SqliteQueueRepository(db)

        first = await repo.enqueue_if_missing_post_item(
            QueueItemType.APPROVAL_REQUEST,
            "p1",
            {"post_id": "p1"},
        )
        second = await repo.enqueue_if_missing_post_item(
            QueueItemType.APPROVAL_REQUEST,
            "p1",
            {"post_id": "p1"},
        )

        assert first is not None
        assert second is None

    async def test_future_items_not_claimed(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        future = datetime.now(timezone.utc) + timedelta(hours=1)
        await repo.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"}, scheduled_at=future)
        assert await repo.claim_next_due(datetime.now(timezone.utc)) is None

    async def test_reschedule_returns_item_to_pending(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        item_id = await repo.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})
        now = datetime.now(timezone.utc)
        await repo.claim_next_due(now)
        await repo.reschedule(item_id, now - timedelta(seconds=1), "temporary failure")
        retried = await repo.claim_next_due(now)
        assert retried is not None
        assert retried.attempts == 2
        assert retried.last_error == "temporary failure"

    async def test_persian_payload_survives_roundtrip(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        await repo.enqueue(QueueItemType.APPROVAL_REQUEST, {"title": "خبر فوری"})
        item = await repo.claim_next_due(datetime.now(timezone.utc))
        assert item.payload["title"] == "خبر فوری"

    async def test_expire_older_than(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        item_id = await repo.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})
        future_cutoff = datetime.now(timezone.utc) + timedelta(days=1)
        assert await repo.expire_older_than(future_cutoff) == 1
        item = await repo.get(item_id)
        assert item.status == QueueStatus.EXPIRED

    async def test_latest_scheduled_publish_for_channel(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        assert await repo.latest_scheduled_publish_for_channel(-100) is None
        early = datetime.now(timezone.utc) + timedelta(minutes=10)
        late = datetime.now(timezone.utc) + timedelta(minutes=40)
        await repo.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": "p1", "chat_id": -100},
            scheduled_at=late,
        )
        await repo.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": "p2", "chat_id": -100},
            scheduled_at=early,
        )
        await repo.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": "p3", "chat_id": -200},
            scheduled_at=late + timedelta(hours=1),
        )
        assert await repo.latest_scheduled_publish_for_channel(-100) == late

    async def test_scheduled_publish_channels_for_post(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        due = datetime.now(timezone.utc) + timedelta(minutes=5)
        first = await repo.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": "p1", "chat_id": -100},
            scheduled_at=due,
        )
        await repo.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": "p1", "chat_id": -200},
            scheduled_at=due,
        )
        await repo.enqueue(
            QueueItemType.SCHEDULED_PUBLISH,
            {"post_id": "p2", "chat_id": -300},
            scheduled_at=due,
        )
        assert await repo.scheduled_publish_channels("p1") == {-100, -200}
        await repo.mark_status(first, QueueStatus.PUBLISHED)
        assert await repo.scheduled_publish_channels("p1") == {-200}

    async def test_has_active_or_successful_post_item(self, db: Database) -> None:
        repo = SqliteQueueRepository(db)
        first = await repo.enqueue(QueueItemType.VPN_TEST, {"post_id": "p1"})
        assert await repo.has_active_or_successful_post_item(
            "p1", {QueueItemType.VPN_TEST}
        )

        await repo.mark_status(first, QueueStatus.FAILED)
        assert not await repo.has_active_or_successful_post_item(
            "p1", {QueueItemType.VPN_TEST}
        )

        second = await repo.enqueue(
            QueueItemType.APPROVAL_REQUEST, {"post_id": "p1"}
        )
        await repo.mark_status(second, QueueStatus.WAITING_APPROVAL)
        assert await repo.has_active_or_successful_post_item(
            "p1", {QueueItemType.APPROVAL_REQUEST}
        )


class TestApprovalRequestRepository:
    """Tests for :class:`SqliteApprovalRequestRepository`."""

    async def test_records_sent_approval_request_once(self, db: Database) -> None:
        repo = SqliteApprovalRequestRepository(db)
        assert await repo.has_requested("p1") is False
        await repo.record_requested("p1")
        await repo.record_requested("p1")
        assert await repo.has_requested("p1") is True

    async def test_reservation_blocks_resend_until_failed(self, db: Database) -> None:
        repo = SqliteApprovalRequestRepository(db)

        assert await repo.reserve_request("p1") is True
        assert await repo.reserve_request("p1") is False
        assert await repo.has_requested("p1") is True

        await repo.mark_failed("p1", "telegram error")
        assert await repo.has_requested("p1") is False
        assert await repo.reserve_request("p1") is True

        await repo.mark_sent("p1")
        assert await repo.reserve_request("p1") is False


class TestChannelRepository:
    """Tests for :class:`SqliteChannelRepository`."""

    async def test_upsert_and_list_destinations(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        channel = DestinationChannel(
            chat_id=-100,
            title="کانال خبری",
            public_id="@news_dest",
            kind=ChannelKind.NEWS,
            publish_usd_price=True,
        )
        await repo.upsert_destination(channel)
        await repo.upsert_destination(channel)
        listed = await repo.list_destinations()
        assert len(listed) == 1
        assert listed[0].title == "کانال خبری"
        assert listed[0].public_id == "@news_dest"
        assert (await repo.list_price_channels())[0].chat_id == -100

    async def test_sources_roundtrip(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source("@source_channel")
        await repo.upsert_source("@source_channel")
        assert await repo.list_sources() == ["@source_channel"]

    async def test_interval_roundtrip_and_get_destination(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_destination(
            DestinationChannel(chat_id=-100, title="خبر", post_interval_minutes=45)
        )
        channel = await repo.get_destination(-100)
        assert channel is not None
        assert channel.post_interval_minutes == 45
        assert await repo.get_destination(-999) is None

    async def test_seed_destination_never_overwrites(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_destination(
            DestinationChannel(chat_id=-100, title="عنوان ربات", post_interval_minutes=10)
        )
        await repo.seed_destination(
            DestinationChannel(chat_id=-100, title="عنوان کانفیگ", post_interval_minutes=99)
        )
        channel = await repo.get_destination(-100)
        assert channel.title == "عنوان ربات"
        assert channel.post_interval_minutes == 10

    async def test_seed_destination_backfills_empty_public_id(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_destination(
            DestinationChannel(chat_id=-100, title="خبر", public_id="")
        )
        await repo.seed_destination(
            DestinationChannel(chat_id=-100, title="ignored", public_id="@dest")
        )
        assert (await repo.get_destination(-100)).public_id == "@dest"
        await repo.seed_destination(
            DestinationChannel(chat_id=-100, title="ignored", public_id="@other")
        )
        assert (await repo.get_destination(-100)).public_id == "@dest"

    async def test_list_source_usernames(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source_details(
            identifier="@alonews", chat_id=-1, title="الو", username="alonews"
        )
        await repo.upsert_source("-100999")
        assert await repo.list_source_usernames() == ["alonews"]

    async def test_seed_source_keeps_disabled_rows_disabled(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source("@source_channel")
        assert await repo.disable_source("@source_channel") is True
        await repo.seed_source("@source_channel")
        assert await repo.list_sources() == []
        await repo.upsert_source("@source_channel")
        assert await repo.list_sources() == ["@source_channel"]

    async def test_disable_source_by_username_or_number(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source_details(
            identifier="@source_channel",
            chat_id=-100123,
            title="منبع",
            username="source_channel",
        )
        assert await repo.disable_source("-100123") is True
        assert await repo.disable_source("@missing") is False

    async def test_source_label_uses_resolved_title_and_username(
        self, db: Database
    ) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source_details(
            identifier="@source_channel",
            chat_id=-100123,
            title="کانال منبع",
            username="source_channel",
        )
        assert await repo.get_source_label(-100123) == "کانال منبع (@source_channel)"

    async def test_disable_missing_sources_and_destinations(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source("@keep")
        await repo.upsert_source("@drop")
        await repo.upsert_destination(DestinationChannel(chat_id=-100, title="Keep"))
        await repo.upsert_destination(DestinationChannel(chat_id=-200, title="Drop"))

        assert await repo.disable_sources_except({"@keep"}) == 1
        assert await repo.disable_destinations_except({-100}) == 1

        assert await repo.list_sources() == ["@keep"]
        assert [channel.chat_id for channel in await repo.list_destinations()] == [-100]


class TestAdminRepository:
    """Tests for :class:`SqliteAdminRepository`."""

    async def test_admin_check(self, db: Database) -> None:
        repo = SqliteAdminRepository(db)
        await repo.upsert(AdminUser(telegram_user_id=42, name="مدیر"))
        assert await repo.is_admin(42) is True
        assert await repo.is_admin(43) is False
        assert await repo.list_user_ids() == [42]

    async def test_replace_all_makes_config_authoritative(self, db: Database) -> None:
        repo = SqliteAdminRepository(db)
        await repo.upsert(AdminUser(telegram_user_id=1))
        await repo.upsert(AdminUser(telegram_user_id=2))

        await repo.replace_all([AdminUser(telegram_user_id=2), AdminUser(telegram_user_id=3)])

        assert await repo.list_user_ids() == [2, 3]


class TestPublishLogRepository:
    """Tests for :class:`SqlitePublishLogRepository`."""

    async def test_publish_log_prevents_duplicates(self, db: Database) -> None:
        repo = SqlitePublishLogRepository(db)
        assert await repo.has_any_delivery_record("p1") is False
        assert await repo.is_published("p1", -100) is False
        await repo.record_published("p1", -100, 555)
        await repo.record_published("p1", -100, 556)
        assert await repo.has_any_delivery_record("p1") is True
        assert await repo.is_published("p1", -100) is True
        assert await repo.published_channels("p1") == {-100}

    async def test_publish_reservation_is_atomic_and_releasable(
        self, db: Database
    ) -> None:
        repo = SqlitePublishLogRepository(db)

        assert await repo.try_reserve_publish("p1", -100, "immediate") is True
        assert await repo.try_reserve_publish("p1", -100, "immediate") is False
        assert await repo.is_published("p1", -100) is True

        await repo.release_reservation("p1", -100)

        assert await repo.is_published("p1", -100) is False

    async def test_scheduled_and_removed_publish_states(self, db: Database) -> None:
        repo = SqlitePublishLogRepository(db)
        scheduled_at = datetime.now(timezone.utc) + timedelta(minutes=5)

        assert await repo.try_reserve_publish("p1", -100, "scheduled") is True
        await repo.mark_scheduled("p1", -100, 777, scheduled_at)

        record = await repo.get_active_record("p1", -100)
        assert record is not None
        assert record.status == "scheduled"
        assert record.mode == "scheduled"
        assert record.message_id == 777
        assert await repo.scheduled_channels("p1") == {-100}
        assert await repo.published_channels("p1") == set()

        await repo.mark_removed("p1", -100)

        assert await repo.has_any_delivery_record("p1") is True
        assert await repo.get_active_record("p1", -100) is None
        assert await repo.try_reserve_publish("p1", -100, "immediate") is True


class TestApprovalMessageRepository:
    """Tests for :class:`SqliteApprovalMessageRepository`."""

    async def test_records_modes_and_deactivation(self, db: Database) -> None:
        repo = SqliteApprovalMessageRepository(db)
        await repo.record_messages(
            [
                ApprovalMessageRef(
                    post_id="p1",
                    admin_user_id=42,
                    chat_id=42,
                    message_id=10,
                )
            ]
        )
        refs = await repo.list_active("p1")
        assert len(refs) == 1
        assert refs[0].delivery_mode == "s"
        assert refs[0].preview_kind == "text"

        await repo.set_delivery_mode("p1", 42, 10, "i")
        refs = await repo.list_active("p1")
        assert refs[0].delivery_mode == "i"

        await repo.set_preview_kind(refs[0].id, "caption")
        refs = await repo.list_active("p1")
        assert refs[0].preview_kind == "caption"

        await repo.deactivate(refs[0].id)
        assert await repo.list_active("p1") == []

        inactive = await repo.list_recent_inactive(
            datetime.now(timezone.utc) - timedelta(hours=1)
        )
        assert [ref.message_id for ref in inactive] == [10]
        await repo.activate(inactive[0].id)
        assert [ref.message_id for ref in await repo.list_active("p1")] == [10]

    async def test_deactivates_messages_for_removed_admins(self, db: Database) -> None:
        repo = SqliteApprovalMessageRepository(db)
        await repo.record_messages(
            [
                ApprovalMessageRef(
                    post_id="p1",
                    admin_user_id=1,
                    chat_id=1,
                    message_id=10,
                ),
                ApprovalMessageRef(
                    post_id="p1",
                    admin_user_id=2,
                    chat_id=2,
                    message_id=20,
                ),
            ]
        )

        assert await repo.deactivate_admins_except({2}) == 1

        refs = await repo.list_active("p1")
        assert [ref.admin_user_id for ref in refs] == [2]


class TestRecurringForwardCampaignRepository:
    """Tests for normalized recurring campaign configuration storage."""

    async def test_replace_and_update_campaign_mirror(self, db: Database) -> None:
        """Campaign times and destinations roundtrip in configured order."""
        repo = SqliteRecurringForwardCampaignRepository(db)
        campaign = RecurringForwardConfig(
            id="daily_ad",
            enabled=True,
            source_post_url="https://t.me/source/10",
            destination_chat_ids=[-1002, -1001],
            times=["09:00", "21:00"],
            show_forward_header=False,
        )

        await repo.replace_all([campaign])
        assert await repo.list_all() == [campaign]

        updated = RecurringForwardConfig(
            id="daily_ad",
            enabled=False,
            source_post_url="https://t.me/source/11",
            destination_chat_ids=[-1001],
            times=["15:30"],
            show_forward_header=True,
        )
        await repo.upsert(updated)
        assert await repo.list_all() == [updated]

        await repo.delete("daily_ad")
        assert await repo.list_all() == []


class TestRecurringForwardOccurrenceRepository:
    """Tests for recurring native Telegram schedule operational state."""

    async def test_occurrence_is_unique_and_roundtrips_messages(
        self, db: Database
    ) -> None:
        repo = SqliteRecurringForwardOccurrenceRepository(db)
        scheduled_at = datetime.now(timezone.utc) + timedelta(hours=2)
        occurrence = RecurringForwardOccurrence(
            campaign_id="daily_ad",
            destination_chat_id=-100,
            source_post_url="https://t.me/source/1",
            show_forward_header=False,
            scheduled_at=scheduled_at,
        )

        occurrence_id = await repo.reserve(occurrence)
        assert occurrence_id is not None
        assert await repo.reserve(occurrence) is None
        await repo.mark_scheduled(occurrence_id, [10, 11])

        rows = await repo.list_future_scheduled(datetime.now(timezone.utc))
        assert len(rows) == 1
        assert rows[0].message_ids == (10, 11)

        await repo.mark_cancelled(occurrence_id)
        assert await repo.list_future_scheduled(datetime.now(timezone.utc)) == []


class TestPriceHistoryRepository:
    """Tests for :class:`SqlitePriceHistoryRepository`."""

    async def test_latest_price_roundtrip(self, db: Database) -> None:
        repo = SqlitePriceHistoryRepository(db)
        assert await repo.get_latest() is None
        await repo.save(DollarPrice(price=Decimal("61500"), source="s1"))
        await repo.save(DollarPrice(price=Decimal("62000.5"), source="s2"))
        latest = await repo.get_latest()
        assert latest.price == Decimal("62000.5")
        assert latest.source == "s2"
