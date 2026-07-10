"""Versioned, repeatable SQLite schema migrations."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from src.infrastructure.db.sqlite.connection import Database

MIGRATIONS: list[tuple[int, str]] = [
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS destination_channels (
            chat_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'news',
            publish_usd_price INTEGER NOT NULL DEFAULT 0,
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS source_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            identifier TEXT NOT NULL UNIQUE,
            enabled INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS admins (
            telegram_user_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS queue_items (
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
        CREATE INDEX IF NOT EXISTS idx_queue_status_due
            ON queue_items (status, scheduled_at);

        CREATE TABLE IF NOT EXISTS publish_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            channel_chat_id INTEGER NOT NULL,
            message_id INTEGER,
            published_at TEXT NOT NULL,
            UNIQUE (post_id, channel_chat_id)
        );

        CREATE TABLE IF NOT EXISTS price_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            price TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT '',
            fetched_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS error_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            context TEXT NOT NULL DEFAULT '',
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """,
    ),
    (
        2,
        """
        ALTER TABLE destination_channels
            ADD COLUMN public_id TEXT NOT NULL DEFAULT '';
        """,
    ),
    (
        3,
        """
        ALTER TABLE source_channels
            ADD COLUMN chat_id INTEGER;

        ALTER TABLE source_channels
            ADD COLUMN title TEXT NOT NULL DEFAULT '';

        ALTER TABLE source_channels
            ADD COLUMN username TEXT NOT NULL DEFAULT '';

        CREATE INDEX IF NOT EXISTS idx_source_channels_chat_id
            ON source_channels (chat_id);
        """,
    ),
    (
        4,
        """
        ALTER TABLE destination_channels
            ADD COLUMN post_interval_minutes INTEGER NOT NULL DEFAULT 30;
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS approval_requests (
            post_id TEXT PRIMARY KEY,
            requested_at TEXT NOT NULL
        );
        """,
    ),
    (
        6,
        """
        ALTER TABLE publish_log
            ADD COLUMN status TEXT NOT NULL DEFAULT 'published';

        ALTER TABLE publish_log
            ADD COLUMN mode TEXT NOT NULL DEFAULT 'immediate';
        """,
    ),
    (
        7,
        """
        CREATE TABLE IF NOT EXISTS approval_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id TEXT NOT NULL,
            admin_user_id INTEGER NOT NULL,
            chat_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            delivery_mode TEXT NOT NULL DEFAULT 's',
            active INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (post_id, admin_user_id, message_id)
        );

        CREATE INDEX IF NOT EXISTS idx_approval_messages_post_active
            ON approval_messages (post_id, active);
        """,
    ),
    (
        8,
        """
        ALTER TABLE publish_log
            ADD COLUMN scheduled_at TEXT;

        ALTER TABLE publish_log
            ADD COLUMN removed_at TEXT;
        """,
    ),
    (
        9,
        """
        ALTER TABLE approval_requests
            ADD COLUMN status TEXT NOT NULL DEFAULT 'sent';

        ALTER TABLE approval_requests
            ADD COLUMN last_error TEXT;

        ALTER TABLE approval_requests
            ADD COLUMN updated_at TEXT;
        """,
    ),
    (
        10,
        """
        ALTER TABLE approval_messages
            ADD COLUMN preview_kind TEXT NOT NULL DEFAULT 'text';

        CREATE TABLE IF NOT EXISTS recurring_forward_occurrences (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id TEXT NOT NULL,
            destination_chat_id INTEGER NOT NULL,
            source_post_url TEXT NOT NULL,
            show_forward_header INTEGER NOT NULL DEFAULT 0,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved',
            last_error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (campaign_id, destination_chat_id, scheduled_at)
        );

        CREATE TABLE IF NOT EXISTS recurring_forward_messages (
            occurrence_id INTEGER NOT NULL,
            message_id INTEGER NOT NULL,
            PRIMARY KEY (occurrence_id, message_id),
            FOREIGN KEY (occurrence_id)
                REFERENCES recurring_forward_occurrences(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_recurring_occurrence_future
            ON recurring_forward_occurrences (status, scheduled_at);
        """,
    ),
    (
        11,
        """
        CREATE TABLE IF NOT EXISTS recurring_forward_campaigns (
            id TEXT PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 1,
            source_post_url TEXT NOT NULL,
            show_forward_header INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS recurring_forward_campaign_times (
            campaign_id TEXT NOT NULL,
            time_of_day TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (campaign_id, time_of_day),
            FOREIGN KEY (campaign_id)
                REFERENCES recurring_forward_campaigns(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recurring_forward_campaign_destinations (
            campaign_id TEXT NOT NULL,
            destination_chat_id INTEGER NOT NULL,
            position INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (campaign_id, destination_chat_id),
            FOREIGN KEY (campaign_id)
                REFERENCES recurring_forward_campaigns(id) ON DELETE CASCADE
        );
        """,
    ),
]


async def apply_migrations(db: Database) -> int:
    """
    Apply all pending schema migrations in order.

    Migrations are idempotent and tracked in ``schema_migrations`` so
    the function is safe to call on every startup.

    Args:
        db: Connected :class:`Database`.

    Returns:
        The number of migrations applied in this call.

    Raises:
        RepositoryError: When a migration statement fails.
    """
    await db.executescript(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version INTEGER PRIMARY KEY,
            applied_at TEXT NOT NULL
        );
        """
    )
    applied = 0
    for version, _script in MIGRATIONS:
        if await _migration_applied(db, version):
            continue
        await _apply_migration(db, version)
        if await _mark_migration_applied(db, version):
            applied += 1
    return applied


async def _migration_applied(db: Database, version: int) -> bool:
    """Return whether a migration version is recorded as applied."""
    row = await db.fetchone(
        "SELECT 1 FROM schema_migrations WHERE version = ?", (version,)
    )
    return row is not None


async def _mark_migration_applied(db: Database, version: int) -> bool:
    """Record one migration version; return whether this call inserted it."""
    cursor = await db.connection.execute(
        """
        INSERT OR IGNORE INTO schema_migrations (version, applied_at)
        VALUES (?, ?)
        """,
        (version, datetime.now(timezone.utc).isoformat()),
    )
    await db.connection.commit()
    return cursor.rowcount == 1


async def _apply_migration(db: Database, version: int) -> None:
    """Apply one migration version using idempotent DDL operations."""
    if version == 1:
        await db.executescript(MIGRATIONS[0][1])
        return
    if version == 2:
        await _ensure_column(
            db,
            "destination_channels",
            "public_id",
            "TEXT NOT NULL DEFAULT ''",
        )
        return
    if version == 3:
        await _ensure_column(db, "source_channels", "chat_id", "INTEGER")
        await _ensure_column(
            db,
            "source_channels",
            "title",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _ensure_column(
            db,
            "source_channels",
            "username",
            "TEXT NOT NULL DEFAULT ''",
        )
        await db.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_source_channels_chat_id
                ON source_channels (chat_id)
            """
        )
        return
    if version == 4:
        await _ensure_column(
            db,
            "destination_channels",
            "post_interval_minutes",
            "INTEGER NOT NULL DEFAULT 30",
        )
        return
    if version == 5:
        await db.executescript(MIGRATIONS[4][1])
        return
    if version == 6:
        await _ensure_column(
            db,
            "publish_log",
            "status",
            "TEXT NOT NULL DEFAULT 'published'",
        )
        await _ensure_column(
            db,
            "publish_log",
            "mode",
            "TEXT NOT NULL DEFAULT 'immediate'",
        )
        return
    if version == 7:
        await db.executescript(MIGRATIONS[6][1])
        return
    if version == 8:
        await _ensure_column(db, "publish_log", "scheduled_at", "TEXT")
        await _ensure_column(db, "publish_log", "removed_at", "TEXT")
        return
    if version == 9:
        await _ensure_column(
            db,
            "approval_requests",
            "status",
            "TEXT NOT NULL DEFAULT 'sent'",
        )
        await _ensure_column(db, "approval_requests", "last_error", "TEXT")
        await _ensure_column(db, "approval_requests", "updated_at", "TEXT")
        await db.execute(
            """
            UPDATE approval_requests
            SET status = COALESCE(NULLIF(status, ''), 'sent'),
                updated_at = COALESCE(updated_at, requested_at)
            """
        )
        return
    if version == 10:
        await _ensure_column(
            db,
            "approval_messages",
            "preview_kind",
            "TEXT NOT NULL DEFAULT 'text'",
        )
        await db.executescript(
            """
            CREATE TABLE IF NOT EXISTS recurring_forward_occurrences (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                campaign_id TEXT NOT NULL,
                destination_chat_id INTEGER NOT NULL,
                source_post_url TEXT NOT NULL,
                show_forward_header INTEGER NOT NULL DEFAULT 0,
                scheduled_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'reserved',
                last_error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (campaign_id, destination_chat_id, scheduled_at)
            );
            CREATE TABLE IF NOT EXISTS recurring_forward_messages (
                occurrence_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (occurrence_id, message_id),
                FOREIGN KEY (occurrence_id)
                    REFERENCES recurring_forward_occurrences(id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_recurring_occurrence_future
                ON recurring_forward_occurrences (status, scheduled_at);
            """
        )
        return
    if version == 11:
        await db.executescript(MIGRATIONS[10][1])
        return
    raise ValueError(f"Unknown migration version: {version}")


async def _ensure_column(
    db: Database, table_name: str, column_name: str, column_definition: str
) -> None:
    """
    Add a SQLite column if it does not already exist.

    SQLite versions used in production do not reliably support
    ``ALTER TABLE ... ADD COLUMN IF NOT EXISTS``. This helper makes ALTER
    migrations restart-safe and safe enough for ``src.run_all`` where the
    main app and collector can race while opening the same database.
    """
    if await _column_exists(db, table_name, column_name):
        return
    try:
        await db.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
    except sqlite3.OperationalError as exc:
        message = str(exc).lower()
        if "duplicate column name" in message and await _column_exists(
            db, table_name, column_name
        ):
            return
        raise


async def _column_exists(db: Database, table_name: str, column_name: str) -> bool:
    """Return whether a table contains a column."""
    rows = await db.fetchall(f"PRAGMA table_info({table_name})")
    return any(row["name"] == column_name for row in rows)
