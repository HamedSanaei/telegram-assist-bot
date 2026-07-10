"""Unit tests for runtime configuration synchronization."""

from __future__ import annotations

import json
from pathlib import Path

from src.infrastructure.db.sqlite.connection import Database
from src.infrastructure.db.sqlite.migrations import apply_migrations
from src.infrastructure.db.sqlite.repositories import (
    SqliteAdminRepository,
    SqliteChannelRepository,
    SqliteRecurringForwardCampaignRepository,
)
from src.workers.config_sync import ConfigSyncWorker


def _config(source: str, destination_chat_id: int, admin_id: int) -> dict:
    """Build a minimal config dictionary for hot-reload tests."""
    return {
        "telegram": {
            "bot_token": "123:abc",
            "approval_bot_token": "456:def",
            "api_id": "11111",
            "api_hash": "hash",
            "source_channels": [source],
            "destination_channels": [
                {
                    "chat_id": destination_chat_id,
                    "title": f"Channel {destination_chat_id}",
                    "public_id": "@dest",
                    "kind": "news",
                    "publish_usd_price": False,
                    "post_interval_minutes": 30,
                }
            ],
            "admin_user_ids": [admin_id],
        },
        "ai": {
            "providers": [
                {
                    "name": "groq",
                    "enabled": True,
                    "api_key": "key",
                    "base_url": "https://api.groq.com/openai/v1",
                    "model": "llama-3.3-70b-versatile",
                }
            ]
        },
        "database": {
            "sqlite_path": "data/app.db",
            "mongodb_connection_string": "mongodb://localhost:27017",
            "mongodb_database": "telegram_admin_bot",
        },
        "scheduler": {
            "timezone": "Asia/Tehran",
            "recurring_forward_lookahead_hours": 24,
            "recurring_forwards": [
                {
                    "id": f"campaign_{admin_id}",
                    "enabled": True,
                    "source_post_url": "https://t.me/source/123",
                    "destination_chat_ids": [destination_chat_id],
                    "show_forward_header": False,
                    "times": ["09:00", "21:00"],
                }
            ],
        },
    }


def _write_config(path: Path, data: dict) -> None:
    """Write a UTF-8 JSON config file."""
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


async def test_runtime_config_sync_is_authoritative(tmp_path: Path) -> None:
    """Changed config lists are mirrored into SQLite without restart."""
    db = Database(tmp_path / "app.db")
    await db.connect()
    await apply_migrations(db)
    config_path = tmp_path / "configuration.json"
    _write_config(config_path, _config("@old", -100, 1))
    worker = ConfigSyncWorker(db, config_path=config_path)
    channels = SqliteChannelRepository(db)
    admins = SqliteAdminRepository(db)
    campaigns = SqliteRecurringForwardCampaignRepository(db)

    assert await worker.sync_if_changed() is True
    assert await channels.list_sources() == ["@old"]
    assert [c.chat_id for c in await channels.list_destinations()] == [-100]
    assert await admins.list_user_ids() == [1]
    assert [item.id for item in await campaigns.list_all()] == ["campaign_1"]

    _write_config(config_path, _config("@new", -200, 2))
    worker._last_mtime_ns = None

    assert await worker.sync_if_changed() is True
    assert await channels.list_sources() == ["@new"]
    assert [c.chat_id for c in await channels.list_destinations()] == [-200]
    assert await admins.list_user_ids() == [2]
    assert [item.id for item in await campaigns.list_all()] == ["campaign_2"]
    await db.close()


async def test_runtime_config_sync_ignores_invalid_json(tmp_path: Path) -> None:
    """A half-written config file does not erase the last good SQLite state."""
    db = Database(tmp_path / "app.db")
    await db.connect()
    await apply_migrations(db)
    config_path = tmp_path / "configuration.json"
    _write_config(config_path, _config("@stable", -100, 1))
    worker = ConfigSyncWorker(db, config_path=config_path)
    channels = SqliteChannelRepository(db)

    assert await worker.sync_if_changed() is True
    config_path.write_text("{ not json", encoding="utf-8")
    worker._last_mtime_ns = None

    assert await worker.sync_if_changed() is False
    assert await channels.list_sources() == ["@stable"]
    await db.close()
