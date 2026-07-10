"""Unit tests for atomic management-panel configuration edits."""

from __future__ import annotations

import json

from src.domain.entities import DestinationChannel
from src.domain.enums import ChannelKind
from src.infrastructure.configuration.atomic_editor import AtomicConfigurationEditor
from src.shared.config import RecurringForwardConfig, load_configuration


def _config() -> dict[str, object]:
    """Return a minimal editable configuration with a sentinel secret."""
    return {
        "telegram": {
            "bot_token": "secret-token",
            "approval_bot_token": "approval-secret",
            "api_id": "1",
            "api_hash": "hash",
            "source_channels": [],
            "destination_channels": [],
            "admin_user_ids": [1],
        },
        "ai": {"providers": []},
        "database": {"mongodb_connection_string": "mongodb://localhost"},
        "scheduler": {
            "timezone": "Asia/Tehran",
            "recurring_forward_lookahead_hours": 24,
            "recurring_forwards": [],
        },
    }


async def test_atomic_editor_preserves_secrets_and_updates_runtime_lists(tmp_path) -> None:
    """Panel-safe edits preserve unknown and sensitive JSON values."""
    path = tmp_path / "configuration.json"
    path.write_text(
        json.dumps(_config(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    editor = AtomicConfigurationEditor(path)

    await editor.add_source("@source")
    await editor.upsert_destination(
        DestinationChannel(
            chat_id=-1001,
            title="خبر",
            public_id="@dest",
            kind=ChannelKind.NEWS,
        )
    )
    await editor.upsert_campaign(
        RecurringForwardConfig(
            id="daily_ad",
            source_post_url="https://t.me/source/10",
            destination_chat_ids=[-1001],
            times=["09:00", "21:00"],
        )
    )

    raw = json.loads(path.read_text(encoding="utf-8"))
    loaded = load_configuration(path)
    assert raw["telegram"]["bot_token"] == "secret-token"
    assert loaded.telegram.source_channels == ["@source"]
    assert loaded.telegram.destination_channels[0].public_id == "@dest"
    assert loaded.scheduler.recurring_forwards[0].id == "daily_ad"
