# mypy: disable-error-code="truthy-bool"
"""Tests for typed, atomic multi-instance configuration generation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from telegram_assist_bot.bootstrap.cli import main
from telegram_assist_bot.bootstrap.instance_config import (
    InstanceConfigurationError,
    render_instance_configuration,
    validate_instance_slug,
)
from telegram_assist_bot.shared.config import ApplicationConfig

ROOT = Path(__file__).parents[3]


@pytest.mark.parametrize("value", ["a", "assistant1", "assistant-01"])
def test_instance_slug_accepts_collision_safe_values(value: str) -> None:
    assert validate_instance_slug(value) == value


@pytest.mark.parametrize(
    "value", ["", "Assistant", "1assistant", "with space", "a" * 33, "../escape"]
)
def test_instance_slug_rejects_unsafe_values(value: str) -> None:
    with pytest.raises(InstanceConfigurationError, match="Instance"):
        validate_instance_slug(value)


def test_renderer_uses_real_model_and_preserves_existing_config(tmp_path: Path) -> None:
    output = tmp_path / "config" / "configuration.json"
    template = ROOT / "config" / "configuration.example.json"
    render_instance_configuration(
        template_path=template,
        output_path=output,
        instance="assistant-one",
        retention_days=7,
        approval_chat_id=-1009001,
        admin_user_id=7001,
        source_username="@source_one",
        destination_name="destination-one",
        destination_id=-1009002,
        destination_username="@destination_one",
        timezone="Asia/Tehran",
    )

    raw = json.loads(output.read_text(encoding="utf-8"))
    loaded = ApplicationConfig.model_validate(raw)
    assert loaded.mongodb.database_name == "telegram_assist_assistant_one"
    assert loaded.media.retention_days == 7
    assert loaded.telegram.bot.approval_chat_id == -1009001
    assert loaded.admins[0].telegram_user_id == 7001
    assert loaded.source_channels[0].username == "source_one"
    assert loaded.destination_channels[0].username == "destination_one"
    assert output.read_text(encoding="utf-8").endswith("\n")

    original = output.read_bytes()
    with pytest.raises(InstanceConfigurationError, match="already exists"):
        render_instance_configuration(
            template_path=template,
            output_path=output,
            instance="assistant-one",
            retention_days=2,
            approval_chat_id=-1009001,
            admin_user_id=7001,
            source_username="source_one",
            destination_name="destination-one",
            destination_id=-1009002,
            destination_username=None,
            timezone="UTC",
        )
    assert output.read_bytes() == original


@pytest.mark.parametrize("retention", [0, 3651, True])
def test_renderer_rejects_invalid_retention(tmp_path: Path, retention: int) -> None:
    with pytest.raises(InstanceConfigurationError, match="Retention"):
        render_instance_configuration(
            template_path=ROOT / "config" / "configuration.example.json",
            output_path=tmp_path / "configuration.json",
            instance="assistant",
            retention_days=retention,
            approval_chat_id=-1009001,
            admin_user_id=7001,
            source_username="source",
            destination_name="destination",
            destination_id=-1009002,
            destination_username=None,
            timezone="UTC",
        )


def test_cli_renders_without_resolving_a_startup_config(tmp_path: Path) -> None:
    output = tmp_path / "configuration.json"
    exit_code = main(
        [
            "render-instance-config",
            "--template",
            str(ROOT / "config" / "configuration.example.json"),
            "--output",
            str(output),
            "--instance",
            "assistant2",
            "--retention-days",
            "2",
            "--approval-chat-id",
            "-1009001",
            "--admin-user-id",
            "7001",
            "--source-username",
            "source",
            "--destination-name",
            "destination",
            "--destination-id",
            "-1009002",
            "--timezone",
            "UTC",
        ],
        environ={},
    )
    assert exit_code == 0
    assert ApplicationConfig.model_validate_json(output.read_text(encoding="utf-8"))
