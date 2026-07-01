"""SQLite implementations of the domain repository interfaces.

All timestamps are stored as ISO-8601 UTC strings; JSON payloads are
written with ``ensure_ascii=False`` so Persian text stays readable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from src.domain.entities import (
    AdminUser,
    DestinationChannel,
    DollarPrice,
    QueueItem,
)
from src.domain.enums import ChannelKind, QueueItemType, QueueStatus
from src.infrastructure.db.sqlite.connection import Database


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string back into a datetime, or ``None``."""
    return datetime.fromisoformat(value) if value else None


class SqliteChannelRepository:
    """SQLite-backed implementation of :class:`ChannelRepository`."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        """Insert or update a destination channel row."""
        await self._db.execute(
            """
            INSERT INTO destination_channels (chat_id, title, kind, publish_usd_price, enabled)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                kind = excluded.kind,
                publish_usd_price = excluded.publish_usd_price,
                enabled = excluded.enabled
            """,
            (
                channel.chat_id,
                channel.title,
                channel.kind.value,
                int(channel.publish_usd_price),
                int(channel.enabled),
            ),
        )

    async def list_destinations(self) -> list[DestinationChannel]:
        """Return all enabled destination channels ordered by title."""
        rows = await self._db.fetchall(
            "SELECT * FROM destination_channels WHERE enabled = 1 ORDER BY title"
        )
        return [self._row_to_destination(row) for row in rows]

    async def list_price_channels(self) -> list[DestinationChannel]:
        """Return enabled channels flagged for USD price publishing."""
        rows = await self._db.fetchall(
            "SELECT * FROM destination_channels WHERE enabled = 1 AND publish_usd_price = 1"
        )
        return [self._row_to_destination(row) for row in rows]

    async def upsert_source(self, identifier: str) -> None:
        """Insert a source channel identifier if missing."""
        await self._db.execute(
            """
            INSERT INTO source_channels (identifier, enabled) VALUES (?, 1)
            ON CONFLICT(identifier) DO UPDATE SET enabled = 1
            """,
            (identifier,),
        )

    async def list_sources(self) -> list[str]:
        """Return all enabled source channel identifiers."""
        rows = await self._db.fetchall(
            "SELECT identifier FROM source_channels WHERE enabled = 1 ORDER BY id"
        )
        return [row["identifier"] for row in rows]

    @staticmethod
    def _row_to_destination(row: object) -> DestinationChannel:
        """Map a database row to a :class:`DestinationChannel`."""
        return DestinationChannel(
            chat_id=row["chat_id"],
            title=row["title"],
            kind=ChannelKind(row["kind"]),
            publish_usd_price=bool(row["publish_usd_price"]),
            enabled=bool(row["enabled"]),
        )


class SqliteAdminRepository:
    """SQLite-backed implementation of :class:`AdminRepository`."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def upsert(self, admin: AdminUser) -> None:
        """Insert or update an admin user row."""
        await self._db.execute(
            """
            INSERT INTO admins (telegram_user_id, name) VALUES (?, ?)
            ON CONFLICT(telegram_user_id) DO UPDATE SET name = excluded.name
            """,
            (admin.telegram_user_id, admin.name),
        )

    async def is_admin(self, telegram_user_id: int) -> bool:
        """Return whether the user id exists in the admins table."""
        row = await self._db.fetchone(
            "SELECT 1 FROM admins WHERE telegram_user_id = ?", (telegram_user_id,)
        )
        return row is not None

    async def list_user_ids(self) -> list[int]:
        """Return all admin Telegram user ids."""
        rows = await self._db.fetchall("SELECT telegram_user_id FROM admins")
        return [row["telegram_user_id"] for row in rows]


class SqliteQueueRepository:
    """SQLite-backed implementation of :class:`QueueRepository`.

    Claiming uses a single conditional ``UPDATE ... RETURNING`` so that
    two workers can never process the same item concurrently.
    """

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def enqueue(
        self,
        item_type: QueueItemType,
        payload: dict[str, object],
        scheduled_at: datetime | None = None,
    ) -> int:
        """Insert a new pending item; return its row id."""
        now = _utcnow_iso()
        due = (scheduled_at or datetime.now(timezone.utc)).isoformat()
        return await self._db.execute(
            """
            INSERT INTO queue_items (type, status, payload, attempts, scheduled_at, created_at, updated_at)
            VALUES (?, 'pending', ?, 0, ?, ?, ?)
            """,
            (item_type.value, json.dumps(payload, ensure_ascii=False), due, now, now),
        )

    async def claim_next_due(self, now: datetime) -> QueueItem | None:
        """Atomically claim the oldest due pending item, or ``None``."""
        now_iso = now.isoformat()
        row = await self._db.fetchone(
            """
            UPDATE queue_items
            SET status = 'processing', attempts = attempts + 1, updated_at = :now
            WHERE id = (
                SELECT id FROM queue_items
                WHERE status = 'pending' AND scheduled_at <= :now
                ORDER BY id LIMIT 1
            ) AND status = 'pending'
            RETURNING *
            """,
            {"now": now_iso},
        )
        return self._row_to_item(row) if row is not None else None

    async def mark_status(
        self, item_id: int, status: QueueStatus, last_error: str | None = None
    ) -> None:
        """Set the final status of an item."""
        await self._db.execute(
            "UPDATE queue_items SET status = ?, last_error = ?, updated_at = ? WHERE id = ?",
            (status.value, last_error, _utcnow_iso(), item_id),
        )

    async def reschedule(
        self, item_id: int, scheduled_at: datetime, last_error: str
    ) -> None:
        """Return an item to ``pending`` for a retry at ``scheduled_at``."""
        await self._db.execute(
            """
            UPDATE queue_items
            SET status = 'pending', scheduled_at = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (scheduled_at.isoformat(), last_error, _utcnow_iso(), item_id),
        )

    async def expire_older_than(self, cutoff: datetime) -> int:
        """Mark stale unfinished items as expired; return affected count."""
        rows = await self._db.fetchall(
            """
            UPDATE queue_items
            SET status = 'expired', updated_at = ?
            WHERE status IN ('pending', 'processing', 'waiting_approval')
              AND created_at < ?
            RETURNING id
            """,
            (_utcnow_iso(), cutoff.isoformat()),
        )
        return len(rows)

    async def get(self, item_id: int) -> QueueItem | None:
        """Return one item by id, or ``None``."""
        row = await self._db.fetchone("SELECT * FROM queue_items WHERE id = ?", (item_id,))
        return self._row_to_item(row) if row is not None else None

    @staticmethod
    def _row_to_item(row: object) -> QueueItem:
        """Map a database row to a :class:`QueueItem`."""
        return QueueItem(
            id=row["id"],
            type=QueueItemType(row["type"]),
            status=QueueStatus(row["status"]),
            payload=json.loads(row["payload"]),
            attempts=row["attempts"],
            last_error=row["last_error"],
            scheduled_at=_parse_dt(row["scheduled_at"]),
            created_at=_parse_dt(row["created_at"]),
            updated_at=_parse_dt(row["updated_at"]),
        )


class SqlitePublishLogRepository:
    """SQLite-backed implementation of :class:`PublishLogRepository`."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def is_published(self, post_id: str, channel_chat_id: int) -> bool:
        """Return whether the post/channel pair exists in the log."""
        row = await self._db.fetchone(
            "SELECT 1 FROM publish_log WHERE post_id = ? AND channel_chat_id = ?",
            (post_id, channel_chat_id),
        )
        return row is not None

    async def record_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        """Insert a publish record; ignores duplicates defensively."""
        await self._db.execute(
            """
            INSERT OR IGNORE INTO publish_log (post_id, channel_chat_id, message_id, published_at)
            VALUES (?, ?, ?, ?)
            """,
            (post_id, channel_chat_id, message_id, _utcnow_iso()),
        )

    async def published_channels(self, post_id: str) -> set[int]:
        """Return chat ids the post was already published to."""
        rows = await self._db.fetchall(
            "SELECT channel_chat_id FROM publish_log WHERE post_id = ?", (post_id,)
        )
        return {row["channel_chat_id"] for row in rows}


class SqlitePriceHistoryRepository:
    """SQLite-backed implementation of :class:`PriceHistoryRepository`.

    Prices are stored as strings to avoid float rounding artifacts.
    """

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def save(self, price: DollarPrice) -> int:
        """Insert one price observation; return its row id."""
        fetched = (price.fetched_at or datetime.now(timezone.utc)).isoformat()
        return await self._db.execute(
            "INSERT INTO price_history (price, source, fetched_at) VALUES (?, ?, ?)",
            (str(price.price), price.source, fetched),
        )

    async def get_latest(self) -> DollarPrice | None:
        """Return the most recently stored price, or ``None``."""
        row = await self._db.fetchone(
            "SELECT * FROM price_history ORDER BY id DESC LIMIT 1"
        )
        if row is None:
            return None
        return DollarPrice(
            id=row["id"],
            price=Decimal(row["price"]),
            source=row["source"],
            fetched_at=_parse_dt(row["fetched_at"]),
        )
