"""SQLite implementations of the domain repository interfaces.

All timestamps are stored as ISO-8601 UTC strings; JSON payloads are
written with ``ensure_ascii=False`` so Persian text stays readable.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from src.domain.entities import (
    AdminUser,
    ApprovalMessageRef,
    DestinationChannel,
    DollarPrice,
    PublishLogEntry,
    QueueItem,
    RecurringForwardOccurrence,
)
from src.domain.enums import ChannelKind, QueueItemType, QueueStatus
from src.infrastructure.db.sqlite.connection import Database
from src.shared.config import RecurringForwardConfig


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    """Parse an ISO-8601 string back into a datetime, or ``None``."""
    return datetime.fromisoformat(value) if value else None


def _as_int_or_none(value: str) -> int | None:
    """Return the value as ``int`` when it is numeric, else ``None``."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class SqliteChannelRepository:
    """SQLite-backed implementation of :class:`ChannelRepository`."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def upsert_destination(self, channel: DestinationChannel) -> None:
        """Insert or update a destination channel row."""
        await self._db.execute(
            """
            INSERT INTO destination_channels
                (chat_id, title, public_id, kind, publish_usd_price, enabled, post_interval_minutes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                title = excluded.title,
                public_id = excluded.public_id,
                kind = excluded.kind,
                publish_usd_price = excluded.publish_usd_price,
                enabled = excluded.enabled,
                post_interval_minutes = excluded.post_interval_minutes
            """,
            (
                channel.chat_id,
                channel.title,
                channel.public_id,
                channel.kind.value,
                int(channel.publish_usd_price),
                int(channel.enabled),
                channel.post_interval_minutes,
            ),
        )

    async def seed_destination(self, channel: DestinationChannel) -> None:
        """
        Insert a destination channel only when it does not exist yet.

        Used by the configuration sync at startup so channel settings
        changed through the management bot are never overwritten by
        ``configuration.json`` values. One exception: an existing row with
        an EMPTY ``public_id`` is backfilled from the config value, so
        adding ``public_id`` to the config after the first start works
        without wiping the database.
        """
        await self._db.execute(
            """
            INSERT INTO destination_channels
                (chat_id, title, public_id, kind, publish_usd_price, enabled, post_interval_minutes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id) DO NOTHING
            """,
            (
                channel.chat_id,
                channel.title,
                channel.public_id,
                channel.kind.value,
                int(channel.publish_usd_price),
                int(channel.enabled),
                channel.post_interval_minutes,
            ),
        )
        if channel.public_id:
            await self._db.execute(
                "UPDATE destination_channels SET public_id = ? "
                "WHERE chat_id = ? AND public_id = ''",
                (channel.public_id, channel.chat_id),
            )

    async def disable_destinations_except(self, chat_ids: set[int]) -> int:
        """Disable destinations whose chat ids are absent from ``chat_ids``."""
        if not chat_ids:
            rows = await self._db.fetchall(
                """
                UPDATE destination_channels
                SET enabled = 0
                WHERE enabled = 1
                RETURNING chat_id
                """
            )
            return len(rows)
        placeholders = ",".join("?" for _ in chat_ids)
        rows = await self._db.fetchall(
            f"""
            UPDATE destination_channels
            SET enabled = 0
            WHERE enabled = 1 AND chat_id NOT IN ({placeholders})
            RETURNING chat_id
            """,
            tuple(chat_ids),
        )
        return len(rows)

    async def get_destination(self, chat_id: int) -> DestinationChannel | None:
        """Return one destination channel regardless of enabled state."""
        row = await self._db.fetchone(
            "SELECT * FROM destination_channels WHERE chat_id = ?", (chat_id,)
        )
        return self._row_to_destination(row) if row is not None else None

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
        """Insert a source channel identifier or re-enable an existing one."""
        await self._db.execute(
            """
            INSERT INTO source_channels (identifier, enabled) VALUES (?, 1)
            ON CONFLICT(identifier) DO UPDATE SET enabled = 1
            """,
            (identifier,),
        )

    async def seed_source(self, identifier: str) -> None:
        """
        Insert a source channel only when it does not exist yet.

        Unlike :meth:`upsert_source`, an existing disabled row stays
        disabled, so sources removed through the management bot are not
        resurrected by the configuration sync at startup.
        """
        await self._db.execute(
            "INSERT OR IGNORE INTO source_channels (identifier, enabled) VALUES (?, 1)",
            (identifier,),
        )

    async def disable_source(self, identifier: str) -> bool:
        """Disable a source channel; return whether a row was affected."""
        rows = await self._db.fetchall(
            """
            UPDATE source_channels
            SET enabled = 0
            WHERE (identifier = ? OR username = ? OR chat_id = ?) AND enabled = 1
            RETURNING id
            """,
            (identifier, identifier.lstrip("@"), _as_int_or_none(identifier)),
        )
        return len(rows) > 0

    async def disable_sources_except(self, identifiers: set[str]) -> int:
        """Disable sources whose identifiers are absent from ``identifiers``."""
        normalized = {identifier.strip() for identifier in identifiers if identifier.strip()}
        if not normalized:
            rows = await self._db.fetchall(
                """
                UPDATE source_channels
                SET enabled = 0
                WHERE enabled = 1
                RETURNING id
                """
            )
            return len(rows)
        placeholders = ",".join("?" for _ in normalized)
        rows = await self._db.fetchall(
            f"""
            UPDATE source_channels
            SET enabled = 0
            WHERE enabled = 1 AND identifier NOT IN ({placeholders})
            RETURNING id
            """,
            tuple(normalized),
        )
        return len(rows)

    async def upsert_source_details(
        self,
        identifier: str,
        chat_id: int,
        title: str,
        username: str,
    ) -> None:
        """Store resolved display metadata for a source channel."""
        await self._db.execute(
            """
            INSERT INTO source_channels (identifier, chat_id, title, username, enabled)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT(identifier) DO UPDATE SET
                chat_id = excluded.chat_id,
                title = excluded.title,
                username = excluded.username,
                enabled = 1
            """,
            (identifier, chat_id, title, username),
        )

    async def get_source_label(self, chat_id: int) -> str | None:
        """Return a readable label for a source channel chat id."""
        row = await self._db.fetchone(
            """
            SELECT identifier, title, username
            FROM source_channels
            WHERE chat_id = ? AND enabled = 1
            ORDER BY id
            LIMIT 1
            """,
            (chat_id,),
        )
        if row is None:
            return None
        title = str(row["title"] or "").strip()
        username = str(row["username"] or "").strip()
        identifier = str(row["identifier"] or "").strip()
        if username and not username.startswith("@"):
            username = f"@{username}"
        if title and username:
            return f"{title} ({username})"
        return title or username or identifier or None

    async def list_sources(self) -> list[str]:
        """Return all enabled source channel identifiers."""
        rows = await self._db.fetchall(
            "SELECT identifier FROM source_channels WHERE enabled = 1 ORDER BY id"
        )
        return [row["identifier"] for row in rows]

    async def list_source_usernames(self) -> list[str]:
        """Return resolved public usernames of enabled source channels."""
        rows = await self._db.fetchall(
            "SELECT username FROM source_channels WHERE enabled = 1 AND username != ''"
        )
        return [row["username"] for row in rows]

    @staticmethod
    def _row_to_destination(row: object) -> DestinationChannel:
        """Map a database row to a :class:`DestinationChannel`."""
        return DestinationChannel(
            chat_id=row["chat_id"],
            title=row["title"],
            public_id=row["public_id"],
            kind=ChannelKind(row["kind"]),
            publish_usd_price=bool(row["publish_usd_price"]),
            enabled=bool(row["enabled"]),
            post_interval_minutes=row["post_interval_minutes"],
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

    async def replace_all(self, admins: list[AdminUser]) -> None:
        """Make the admins table exactly match the provided admin list."""
        user_ids = {admin.telegram_user_id for admin in admins}
        if user_ids:
            placeholders = ",".join("?" for _ in user_ids)
            await self._db.execute(
                f"DELETE FROM admins WHERE telegram_user_id NOT IN ({placeholders})",
                tuple(user_ids),
            )
        else:
            await self._db.execute("DELETE FROM admins")
        for admin in admins:
            await self.upsert(admin)

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

    async def enqueue_if_missing_post_item(
        self,
        item_type: QueueItemType,
        post_id: str,
        payload: dict[str, object],
        scheduled_at: datetime | None = None,
    ) -> int | None:
        """Atomically insert one idempotent post pipeline item."""
        now = _utcnow_iso()
        due = (scheduled_at or datetime.now(timezone.utc)).isoformat()
        statuses = (
            QueueStatus.PENDING.value,
            QueueStatus.PROCESSING.value,
            QueueStatus.WAITING_APPROVAL.value,
            QueueStatus.APPROVED.value,
            QueueStatus.COMPLETED.value,
            QueueStatus.PUBLISHED.value,
        )
        placeholders = ",".join("?" for _ in statuses)
        cursor = await self._db.connection.execute(
            f"""
            INSERT INTO queue_items
                (type, status, payload, attempts, scheduled_at, created_at, updated_at)
            SELECT ?, 'pending', ?, 0, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM queue_items
                WHERE type = ?
                  AND json_extract(payload, '$.post_id') = ?
                  AND status IN ({placeholders})
            )
            RETURNING id
            """,
            (
                item_type.value,
                json.dumps(payload, ensure_ascii=False),
                due,
                now,
                now,
                item_type.value,
                post_id,
                *statuses,
            ),
        )
        row = await cursor.fetchone()
        await self._db.connection.commit()
        return int(row["id"]) if row is not None else None

    async def claim_next_due(
        self, now: datetime, allowed_types: set[QueueItemType] | None = None
    ) -> QueueItem | None:
        """Atomically claim the oldest due item matching optional types."""
        now_iso = now.isoformat()
        params: dict[str, object] = {"now": now_iso}
        type_filter = ""
        if allowed_types:
            names: list[str] = []
            for index, item_type in enumerate(sorted(allowed_types, key=lambda x: x.value)):
                key = f"type_{index}"
                names.append(f":{key}")
                params[key] = item_type.value
            type_filter = f" AND type IN ({','.join(names)})"
        row = await self._db.fetchone(
            f"""
            UPDATE queue_items
            SET status = 'processing', attempts = attempts + 1, updated_at = :now
            WHERE id = (
                SELECT id FROM queue_items
                WHERE status = 'pending' AND scheduled_at <= :now
                {type_filter}
                ORDER BY id LIMIT 1
            ) AND status = 'pending'
            RETURNING *
            """,
            params,
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

    async def has_active_or_successful_post_item(
        self, post_id: str, item_types: set[QueueItemType]
    ) -> bool:
        """Return whether the post has a non-failed item of the requested types."""
        if not item_types:
            return False
        placeholders = ",".join("?" for _ in item_types)
        statuses = (
            QueueStatus.PENDING.value,
            QueueStatus.PROCESSING.value,
            QueueStatus.WAITING_APPROVAL.value,
            QueueStatus.APPROVED.value,
            QueueStatus.COMPLETED.value,
            QueueStatus.PUBLISHED.value,
        )
        status_placeholders = ",".join("?" for _ in statuses)
        row = await self._db.fetchone(
            f"""
            SELECT 1 FROM queue_items
            WHERE json_extract(payload, '$.post_id') = ?
              AND type IN ({placeholders})
              AND status IN ({status_placeholders})
            LIMIT 1
            """,
            (
                post_id,
                *(item_type.value for item_type in item_types),
                *statuses,
            ),
        )
        return row is not None

    async def get(self, item_id: int) -> QueueItem | None:
        """Return one item by id, or ``None``."""
        row = await self._db.fetchone("SELECT * FROM queue_items WHERE id = ?", (item_id,))
        return self._row_to_item(row) if row is not None else None

    async def latest_scheduled_publish_for_channel(
        self, channel_chat_id: int
    ) -> datetime | None:
        """Return the latest pending scheduled-publish time for a channel."""
        row = await self._db.fetchone(
            """
            SELECT MAX(scheduled_at) AS latest FROM queue_items
            WHERE type = 'scheduled_publish'
              AND status IN ('pending', 'processing')
              AND json_extract(payload, '$.chat_id') = ?
            """,
            (channel_chat_id,),
        )
        return _parse_dt(row["latest"]) if row is not None else None

    async def scheduled_publish_channels(self, post_id: str) -> set[int]:
        """Return chat ids with a pending scheduled publish of this post."""
        rows = await self._db.fetchall(
            """
            SELECT json_extract(payload, '$.chat_id') AS chat_id FROM queue_items
            WHERE type = 'scheduled_publish'
              AND status IN ('pending', 'processing')
              AND json_extract(payload, '$.post_id') = ?
            """,
            (post_id,),
        )
        return {int(row["chat_id"]) for row in rows if row["chat_id"] is not None}

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


class SqliteApprovalRequestRepository:
    """SQLite-backed idempotency store for approval-bot requests."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def has_requested(self, post_id: str) -> bool:
        """Return whether an approval request already entered dispatch."""
        row = await self._db.fetchone(
            """
            SELECT 1 FROM approval_requests
            WHERE post_id = ? AND status IN ('reserved', 'sent')
            """,
            (post_id,),
        )
        return row is not None

    async def record_requested(self, post_id: str) -> None:
        """Record that the approval request was sent successfully."""
        now = _utcnow_iso()
        await self._db.execute(
            """
            INSERT INTO approval_requests
                (post_id, requested_at, status, last_error, updated_at)
            VALUES (?, ?, 'sent', NULL, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                status = 'sent',
                last_error = NULL,
                updated_at = excluded.updated_at
            """,
            (post_id, now, now),
        )

    async def reserve_request(self, post_id: str) -> bool:
        """Reserve an approval request unless it is already reserved or sent."""
        now = _utcnow_iso()
        cursor = await self._db.connection.execute(
            """
            INSERT OR IGNORE INTO approval_requests
                (post_id, requested_at, status, last_error, updated_at)
            VALUES (?, ?, 'reserved', NULL, ?)
            """,
            (post_id, now, now),
        )
        await self._db.connection.commit()
        if cursor.rowcount == 1:
            return True
        row = await self._db.fetchone(
            "SELECT status FROM approval_requests WHERE post_id = ?", (post_id,)
        )
        if row is None or row["status"] != "failed":
            return False
        cursor = await self._db.connection.execute(
            """
            UPDATE approval_requests
            SET status = 'reserved', last_error = NULL, updated_at = ?
            WHERE post_id = ? AND status = 'failed'
            """,
            (now, post_id),
        )
        await self._db.connection.commit()
        return cursor.rowcount == 1

    async def mark_sent(self, post_id: str) -> None:
        """Mark a reserved approval request as sent."""
        await self._db.execute(
            """
            UPDATE approval_requests
            SET status = 'sent', last_error = NULL, updated_at = ?
            WHERE post_id = ?
            """,
            (_utcnow_iso(), post_id),
        )

    async def mark_failed(self, post_id: str, error: str) -> None:
        """Mark a reserved approval request as failed."""
        await self._db.execute(
            """
            UPDATE approval_requests
            SET status = 'failed', last_error = ?, updated_at = ?
            WHERE post_id = ?
            """,
            (error, _utcnow_iso(), post_id),
        )

    async def list_requested_post_ids(self) -> list[str]:
        """Return post ids already reserved or sent to the approval bot."""
        rows = await self._db.fetchall(
            """
            SELECT post_id FROM approval_requests
            WHERE status IN ('reserved', 'sent')
            ORDER BY requested_at, post_id
            """
        )
        return [row["post_id"] for row in rows]


class SqliteApprovalMessageRepository:
    """SQLite-backed store for approval-bot message references."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def record_messages(self, refs: list[ApprovalMessageRef]) -> None:
        """Persist delivered approval messages."""
        now = _utcnow_iso()
        for ref in refs:
            await self._db.execute(
                """
                INSERT INTO approval_messages
                    (post_id, admin_user_id, chat_id, message_id, delivery_mode,
                     preview_kind, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(post_id, admin_user_id, message_id) DO UPDATE SET
                    chat_id = excluded.chat_id,
                    delivery_mode = excluded.delivery_mode,
                    preview_kind = excluded.preview_kind,
                    active = 1,
                    updated_at = excluded.updated_at
                """,
                (
                    ref.post_id,
                    ref.admin_user_id,
                    ref.chat_id,
                    ref.message_id,
                    ref.delivery_mode,
                    ref.preview_kind,
                    int(ref.active),
                    now,
                    now,
                ),
            )

    async def list_active(self, post_id: str) -> list[ApprovalMessageRef]:
        """Return active approval messages for a post."""
        rows = await self._db.fetchall(
            """
            SELECT * FROM approval_messages
            WHERE post_id = ? AND active = 1
            ORDER BY id
            """,
            (post_id,),
        )
        return [self._row_to_ref(row) for row in rows]

    async def set_delivery_mode(
        self, post_id: str, chat_id: int, message_id: int, delivery_mode: str
    ) -> None:
        """Update delivery mode for one approval message."""
        await self._db.execute(
            """
            UPDATE approval_messages
            SET delivery_mode = ?, updated_at = ?
            WHERE post_id = ? AND chat_id = ? AND message_id = ? AND active = 1
            """,
            (delivery_mode, _utcnow_iso(), post_id, chat_id, message_id),
        )

    async def deactivate(self, message_ref_id: int) -> None:
        """Mark one approval message as inactive."""
        await self._db.execute(
            """
            UPDATE approval_messages
            SET active = 0, updated_at = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), message_ref_id),
        )

    async def activate(self, message_ref_id: int) -> None:
        """Reactivate one approval message after a successful repair edit."""
        await self._db.execute(
            """
            UPDATE approval_messages
            SET active = 1, updated_at = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), message_ref_id),
        )

    async def list_recent_inactive(
        self, updated_since: datetime, limit: int = 500
    ) -> list[ApprovalMessageRef]:
        """Return recently inactive message references for startup repair."""
        rows = await self._db.fetchall(
            """
            SELECT * FROM approval_messages
            WHERE active = 0 AND updated_at >= ?
            ORDER BY updated_at DESC, id DESC
            LIMIT ?
            """,
            (updated_since.isoformat(), max(1, limit)),
        )
        return [self._row_to_ref(row) for row in rows]

    async def list_active_post_ids(self) -> list[str]:
        """Return post ids that have active approval messages."""
        rows = await self._db.fetchall(
            """
            SELECT DISTINCT post_id FROM approval_messages
            WHERE active = 1
            ORDER BY post_id
            """
        )
        return [row["post_id"] for row in rows]

    async def deactivate_admins_except(self, admin_user_ids: set[int]) -> int:
        """Deactivate approval messages for admins absent from ``admin_user_ids``."""
        if not admin_user_ids:
            rows = await self._db.fetchall(
                """
                UPDATE approval_messages
                SET active = 0, updated_at = ?
                WHERE active = 1
                RETURNING id
                """,
                (_utcnow_iso(),),
            )
            return len(rows)
        placeholders = ",".join("?" for _ in admin_user_ids)
        rows = await self._db.fetchall(
            f"""
            UPDATE approval_messages
            SET active = 0, updated_at = ?
            WHERE active = 1 AND admin_user_id NOT IN ({placeholders})
            RETURNING id
            """,
            (_utcnow_iso(), *admin_user_ids),
        )
        return len(rows)

    @staticmethod
    def _row_to_ref(row: object) -> ApprovalMessageRef:
        """Map a database row to :class:`ApprovalMessageRef`."""
        return ApprovalMessageRef(
            id=row["id"],
            post_id=row["post_id"],
            admin_user_id=row["admin_user_id"],
            chat_id=row["chat_id"],
            message_id=row["message_id"],
            delivery_mode=row["delivery_mode"],
            preview_kind=row["preview_kind"],
            active=bool(row["active"]),
        )


class SqlitePublishLogRepository:
    """SQLite-backed implementation of :class:`PublishLogRepository`."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def has_any_delivery_record(self, post_id: str) -> bool:
        """Return whether the post has any publish-log row."""
        row = await self._db.fetchone(
            "SELECT 1 FROM publish_log WHERE post_id = ? LIMIT 1",
            (post_id,),
        )
        return row is not None

    async def is_published(self, post_id: str, channel_chat_id: int) -> bool:
        """Return whether the post/channel pair has an active publish state."""
        row = await self._db.fetchone(
            """
            SELECT 1 FROM publish_log
            WHERE post_id = ? AND channel_chat_id = ?
              AND status IN ('reserved', 'published')
              AND mode = 'immediate'
            """,
            (post_id, channel_chat_id),
        )
        return row is not None

    async def record_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        """Insert a successful publish record; ignore duplicates defensively."""
        await self._db.execute(
            """
            INSERT OR IGNORE INTO publish_log
                (post_id, channel_chat_id, message_id, published_at,
                 scheduled_at, removed_at, status, mode)
            VALUES (?, ?, ?, ?, NULL, NULL, 'published', 'immediate')
            """,
            (post_id, channel_chat_id, message_id, _utcnow_iso()),
        )

    async def try_reserve_publish(
        self, post_id: str, channel_chat_id: int, mode: str
    ) -> bool:
        """Atomically reserve a post/channel pair before Telegram publishing."""
        now = _utcnow_iso()
        cursor = await self._db.connection.execute(
            """
            INSERT OR IGNORE INTO publish_log
                (post_id, channel_chat_id, message_id, published_at,
                 scheduled_at, removed_at, status, mode)
            VALUES (?, ?, NULL, ?, NULL, NULL, 'reserved', ?)
            """,
            (post_id, channel_chat_id, now, mode),
        )
        await self._db.connection.commit()
        if cursor.rowcount == 1:
            return True
        cursor = await self._db.connection.execute(
            """
            UPDATE publish_log
            SET message_id = NULL,
                published_at = ?,
                scheduled_at = NULL,
                removed_at = NULL,
                status = 'reserved',
                mode = ?
            WHERE post_id = ? AND channel_chat_id = ? AND status = 'removed'
            """,
            (now, mode, post_id, channel_chat_id),
        )
        await self._db.connection.commit()
        return cursor.rowcount == 1

    async def mark_published(
        self, post_id: str, channel_chat_id: int, message_id: int
    ) -> None:
        """Mark a reserved post/channel pair as published."""
        await self._db.execute(
            """
            UPDATE publish_log
            SET message_id = ?,
                published_at = ?,
                scheduled_at = NULL,
                removed_at = NULL,
                status = 'published',
                mode = 'immediate'
            WHERE post_id = ? AND channel_chat_id = ?
            """,
            (message_id, _utcnow_iso(), post_id, channel_chat_id),
        )

    async def mark_scheduled(
        self,
        post_id: str,
        channel_chat_id: int,
        message_id: int,
        scheduled_at: datetime,
    ) -> None:
        """Mark a reserved post/channel pair as scheduled in Telegram."""
        await self._db.execute(
            """
            UPDATE publish_log
            SET message_id = ?,
                published_at = ?,
                scheduled_at = ?,
                removed_at = NULL,
                status = 'scheduled',
                mode = 'scheduled'
            WHERE post_id = ? AND channel_chat_id = ?
            """,
            (
                message_id,
                _utcnow_iso(),
                scheduled_at.isoformat(),
                post_id,
                channel_chat_id,
            ),
        )

    async def release_reservation(self, post_id: str, channel_chat_id: int) -> None:
        """Remove an unpublished reservation after Telegram failure."""
        await self._db.execute(
            """
            DELETE FROM publish_log
            WHERE post_id = ? AND channel_chat_id = ?
              AND status = 'reserved' AND message_id IS NULL
            """,
            (post_id, channel_chat_id),
        )

    async def published_channels(self, post_id: str) -> set[int]:
        """Return chat ids the post was immediately published to."""
        rows = await self._db.fetchall(
            """
            SELECT channel_chat_id FROM publish_log
            WHERE post_id = ?
              AND status IN ('reserved', 'published')
              AND mode = 'immediate'
            """,
            (post_id,),
        )
        return {row["channel_chat_id"] for row in rows}

    async def scheduled_channels(self, post_id: str) -> set[int]:
        """Return chat ids the post was natively scheduled to."""
        rows = await self._db.fetchall(
            """
            SELECT channel_chat_id FROM publish_log
            WHERE post_id = ?
              AND status IN ('reserved', 'scheduled')
              AND mode = 'scheduled'
            """,
            (post_id,),
        )
        return {row["channel_chat_id"] for row in rows}

    async def get_active_record(
        self, post_id: str, channel_chat_id: int
    ) -> PublishLogEntry | None:
        """Return the active publish/schedule row for a post/channel."""
        row = await self._db.fetchone(
            """
            SELECT * FROM publish_log
            WHERE post_id = ? AND channel_chat_id = ?
              AND status IN ('reserved', 'published', 'scheduled')
            """,
            (post_id, channel_chat_id),
        )
        return self._row_to_publish_entry(row) if row is not None else None

    async def mark_removed(self, post_id: str, channel_chat_id: int) -> None:
        """Mark a published/scheduled message as removed from Telegram."""
        await self._db.execute(
            """
            UPDATE publish_log
            SET status = 'removed',
                removed_at = ?,
                message_id = NULL
            WHERE post_id = ? AND channel_chat_id = ?
            """,
            (_utcnow_iso(), post_id, channel_chat_id),
        )

    async def last_published_at(self, channel_chat_id: int) -> datetime | None:
        """Return the most recent publish time on the channel, or ``None``."""
        row = await self._db.fetchone(
            """
            SELECT MAX(published_at) AS latest FROM publish_log
            WHERE channel_chat_id = ? AND status = 'published'
            """,
            (channel_chat_id,),
        )
        return _parse_dt(row["latest"]) if row is not None else None

    async def list_history(self, post_id: str) -> list[PublishLogEntry]:
        """Return every persisted destination state for one post."""
        rows = await self._db.fetchall(
            """
            SELECT * FROM publish_log
            WHERE post_id = ?
            ORDER BY channel_chat_id
            """,
            (post_id,),
        )
        return [self._row_to_publish_entry(row) for row in rows]

    @staticmethod
    def _row_to_publish_entry(row: object) -> PublishLogEntry:
        """Map a database row to :class:`PublishLogEntry`."""
        return PublishLogEntry(
            post_id=row["post_id"],
            channel_chat_id=row["channel_chat_id"],
            mode=row["mode"],
            status=row["status"],
            message_id=row["message_id"],
            published_at=_parse_dt(row["published_at"]),
            scheduled_at=_parse_dt(row["scheduled_at"]),
            removed_at=_parse_dt(row["removed_at"]),
        )


class SqliteRecurringForwardCampaignRepository:
    """SQLite mirror of recurring campaign definitions from configuration."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def replace_all(self, campaigns: list[RecurringForwardConfig]) -> None:
        """Atomically replace the campaign mirror with authoritative config."""
        connection = self._db.connection
        await connection.execute("BEGIN IMMEDIATE")
        try:
            await connection.execute("DELETE FROM recurring_forward_campaigns")
            for campaign in campaigns:
                await self._write_campaign(connection, campaign)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise

    async def upsert(self, campaign: RecurringForwardConfig) -> None:
        """Atomically insert or update one campaign and its child rows."""
        connection = self._db.connection
        await connection.execute("BEGIN IMMEDIATE")
        try:
            await self._write_campaign(connection, campaign)
            await connection.commit()
        except Exception:
            await connection.rollback()
            raise

    async def delete(self, campaign_id: str) -> None:
        """Delete one mirrored campaign and cascade its times/destinations."""
        await self._db.execute(
            "DELETE FROM recurring_forward_campaigns WHERE id = ?",
            (campaign_id,),
        )

    async def set_enabled(self, campaign_id: str, enabled: bool) -> None:
        """Update one mirrored campaign's enabled state."""
        await self._db.execute(
            """
            UPDATE recurring_forward_campaigns
            SET enabled = ?, updated_at = ?
            WHERE id = ?
            """,
            (int(enabled), _utcnow_iso(), campaign_id),
        )

    async def list_all(self) -> list[RecurringForwardConfig]:
        """Return all mirrored campaigns with ordered times and destinations."""
        rows = await self._db.fetchall(
            "SELECT * FROM recurring_forward_campaigns ORDER BY id"
        )
        campaigns: list[RecurringForwardConfig] = []
        for row in rows:
            time_rows = await self._db.fetchall(
                """
                SELECT time_of_day FROM recurring_forward_campaign_times
                WHERE campaign_id = ? ORDER BY position, time_of_day
                """,
                (row["id"],),
            )
            destination_rows = await self._db.fetchall(
                """
                SELECT destination_chat_id
                FROM recurring_forward_campaign_destinations
                WHERE campaign_id = ? ORDER BY position, destination_chat_id
                """,
                (row["id"],),
            )
            campaigns.append(
                RecurringForwardConfig(
                    id=row["id"],
                    enabled=bool(row["enabled"]),
                    source_post_url=row["source_post_url"],
                    show_forward_header=bool(row["show_forward_header"]),
                    times=[item["time_of_day"] for item in time_rows],
                    destination_chat_ids=[
                        item["destination_chat_id"] for item in destination_rows
                    ],
                )
            )
        return campaigns

    @staticmethod
    async def _write_campaign(
        connection: Any, campaign: RecurringForwardConfig
    ) -> None:
        """Write one campaign and replace its normalized child rows."""
        await connection.execute(
            """
            INSERT INTO recurring_forward_campaigns
                (id, enabled, source_post_url, show_forward_header, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                enabled = excluded.enabled,
                source_post_url = excluded.source_post_url,
                show_forward_header = excluded.show_forward_header,
                updated_at = excluded.updated_at
            """,
            (
                campaign.id,
                int(campaign.enabled),
                campaign.source_post_url,
                int(campaign.show_forward_header),
                _utcnow_iso(),
            ),
        )
        await connection.execute(
            "DELETE FROM recurring_forward_campaign_times WHERE campaign_id = ?",
            (campaign.id,),
        )
        await connection.execute(
            "DELETE FROM recurring_forward_campaign_destinations WHERE campaign_id = ?",
            (campaign.id,),
        )
        for position, value in enumerate(campaign.times):
            await connection.execute(
                """
                INSERT INTO recurring_forward_campaign_times
                    (campaign_id, time_of_day, position)
                VALUES (?, ?, ?)
                """,
                (campaign.id, value, position),
            )
        for position, chat_id in enumerate(campaign.destination_chat_ids):
            await connection.execute(
                """
                INSERT INTO recurring_forward_campaign_destinations
                    (campaign_id, destination_chat_id, position)
                VALUES (?, ?, ?)
                """,
                (campaign.id, chat_id, position),
            )


class SqliteRecurringForwardOccurrenceRepository:
    """SQLite operational state for recurring Telegram schedule campaigns."""

    def __init__(self, db: Database) -> None:
        """Args: db: Connected database wrapper."""
        self._db = db

    async def reserve(self, occurrence: RecurringForwardOccurrence) -> int | None:
        """Reserve a unique occurrence; return ``None`` when already known."""
        now = _utcnow_iso()
        cursor = await self._db.connection.execute(
            """
            INSERT OR IGNORE INTO recurring_forward_occurrences
                (campaign_id, destination_chat_id, source_post_url,
                 show_forward_header, scheduled_at, status, last_error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 'reserved', NULL, ?, ?)
            """,
            (
                occurrence.campaign_id,
                occurrence.destination_chat_id,
                occurrence.source_post_url,
                int(occurrence.show_forward_header),
                occurrence.scheduled_at.isoformat(),
                now,
                now,
            ),
        )
        await self._db.connection.commit()
        return int(cursor.lastrowid) if cursor.rowcount == 1 else None

    async def mark_scheduled(self, occurrence_id: int, message_ids: list[int]) -> None:
        """Record successful native Telegram schedule message ids."""
        await self._db.execute(
            """
            UPDATE recurring_forward_occurrences
            SET status = 'scheduled', last_error = NULL, updated_at = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), occurrence_id),
        )
        for message_id in message_ids:
            await self._db.execute(
                """
                INSERT OR IGNORE INTO recurring_forward_messages
                    (occurrence_id, message_id)
                VALUES (?, ?)
                """,
                (occurrence_id, message_id),
            )

    async def mark_failed(self, occurrence_id: int, error: str) -> None:
        """Record a failed scheduling attempt without duplicating it."""
        await self._db.execute(
            """
            UPDATE recurring_forward_occurrences
            SET status = 'failed', last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (error[:2000], _utcnow_iso(), occurrence_id),
        )

    async def list_future_scheduled(
        self, now: datetime
    ) -> list[RecurringForwardOccurrence]:
        """Return all future scheduled occurrences for reconciliation."""
        rows = await self._db.fetchall(
            """
            SELECT * FROM recurring_forward_occurrences
            WHERE status = 'scheduled' AND scheduled_at > ?
            ORDER BY scheduled_at
            """,
            (now.isoformat(),),
        )
        result: list[RecurringForwardOccurrence] = []
        for row in rows:
            message_rows = await self._db.fetchall(
                """
                SELECT message_id FROM recurring_forward_messages
                WHERE occurrence_id = ? ORDER BY message_id
                """,
                (row["id"],),
            )
            result.append(
                RecurringForwardOccurrence(
                    id=row["id"],
                    campaign_id=row["campaign_id"],
                    destination_chat_id=row["destination_chat_id"],
                    source_post_url=row["source_post_url"],
                    show_forward_header=bool(row["show_forward_header"]),
                    scheduled_at=_parse_dt(row["scheduled_at"]) or now,
                    status=row["status"],
                    message_ids=tuple(item["message_id"] for item in message_rows),
                    last_error=row["last_error"],
                )
            )
        return result

    async def mark_cancelled(self, occurrence_id: int) -> None:
        """Mark one future native schedule occurrence as cancelled."""
        await self._db.execute(
            """
            UPDATE recurring_forward_occurrences
            SET status = 'cancelled', updated_at = ?
            WHERE id = ?
            """,
            (_utcnow_iso(), occurrence_id),
        )


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
