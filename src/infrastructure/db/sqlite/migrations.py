"""Versioned, repeatable SQLite schema migrations."""

from __future__ import annotations

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
    row = await db.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
    current = row["v"] if row and row["v"] is not None else 0

    applied = 0
    for version, script in MIGRATIONS:
        if version <= current:
            continue
        await db.executescript(script)
        await db.execute(
            "INSERT INTO schema_migrations (version, applied_at) VALUES (?, ?)",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        applied += 1
    return applied
