"""Unit tests for the SQLite repositories and migrations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from src.domain.entities import AdminUser, DestinationChannel, DollarPrice
from src.domain.enums import ChannelKind, QueueItemType, QueueStatus
from src.infrastructure.db.sqlite.connection import Database
from src.infrastructure.db.sqlite.migrations import apply_migrations
from src.infrastructure.db.sqlite.repositories import (
    SqliteAdminRepository,
    SqliteChannelRepository,
    SqlitePriceHistoryRepository,
    SqlitePublishLogRepository,
    SqliteQueueRepository,
)


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


class TestChannelRepository:
    """Tests for :class:`SqliteChannelRepository`."""

    async def test_upsert_and_list_destinations(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        channel = DestinationChannel(
            chat_id=-100,
            title="کانال خبری",
            kind=ChannelKind.NEWS,
            publish_usd_price=True,
        )
        await repo.upsert_destination(channel)
        await repo.upsert_destination(channel)
        listed = await repo.list_destinations()
        assert len(listed) == 1
        assert listed[0].title == "کانال خبری"
        assert (await repo.list_price_channels())[0].chat_id == -100

    async def test_sources_roundtrip(self, db: Database) -> None:
        repo = SqliteChannelRepository(db)
        await repo.upsert_source("@source_channel")
        await repo.upsert_source("@source_channel")
        assert await repo.list_sources() == ["@source_channel"]


class TestAdminRepository:
    """Tests for :class:`SqliteAdminRepository`."""

    async def test_admin_check(self, db: Database) -> None:
        repo = SqliteAdminRepository(db)
        await repo.upsert(AdminUser(telegram_user_id=42, name="مدیر"))
        assert await repo.is_admin(42) is True
        assert await repo.is_admin(43) is False
        assert await repo.list_user_ids() == [42]


class TestPublishLogRepository:
    """Tests for :class:`SqlitePublishLogRepository`."""

    async def test_publish_log_prevents_duplicates(self, db: Database) -> None:
        repo = SqlitePublishLogRepository(db)
        assert await repo.is_published("p1", -100) is False
        await repo.record_published("p1", -100, 555)
        await repo.record_published("p1", -100, 556)
        assert await repo.is_published("p1", -100) is True
        assert await repo.published_channels("p1") == {-100}


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
